"""Behavior of the HTTP server: routes, the event stream and the poll rule."""

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmap.plan import PlanStore
from agentmap.server import CellCache, Dashboard, make_server, sse_patch
from agentmap.t3 import AgentThread, Touch
from agentmap.treemap import Cell

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
CELLS = [Cell("src", "src", 1, 0, 0, 1080, 600, 10)]


def thread(id, title="Work", touched=()):
    return AgentThread(id=id, parent_id=None, title=title, provider="claude", model=None,
                       state="working", created_at=T0, updated_at=T0, now=None, thought=None,
                       last_said=None, thought_at=None, said_at=None,
                       touched=tuple(Touch(path, T0) for path in touched))


class FakeReader:
    def __init__(self, threads):
        self.threads = threads
        self.dirty = True

    def changed(self):
        dirty, self.dirty = self.dirty, False
        return dirty

    def snapshot(self, now):
        return list(self.threads)

    def thread_project(self, thread_id):
        return {"other": False}.get(thread_id, True)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def make_dashboard(tmp, threads=(), **kw):
    reader = FakeReader(list(threads))
    store = PlanStore(Path(tmp) / "plan.jsonl")
    clock = Clock()
    dash = Dashboard(reader, store, lambda: CELLS, "demo", clock=clock, **kw)
    return dash, reader, store, clock


class SseTest(unittest.TestCase):
    def test_each_html_line_gets_its_own_elements_data_line(self):
        self.assertEqual(
            sse_patch("<main id=\"app\">\n<p>x</p></main>"),
            b"event: datastar-patch-elements\n"
            b"data: elements <main id=\"app\">\n"
            b"data: elements <p>x</p></main>\n\n")

    def test_carriage_returns_cannot_break_the_event(self):
        self.assertEqual(sse_patch("a\r\nb\rc"),
                         b"event: datastar-patch-elements\n"
                         b"data: elements a\ndata: elements b\ndata: elements c\n\n")


class TickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dash, self.reader, self.store, self.clock = make_dashboard(
            self.tmp.name, [thread("t1", "First")])

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_first_tick_publishes_the_app(self):
        self.assertTrue(self.dash.tick())
        self.assertIn("First", self.dash.hub.html)

    def test_a_tick_without_news_publishes_nothing(self):
        self.dash.tick()
        self.assertFalse(self.dash.tick())

    def test_a_database_change_publishes_new_html(self):
        self.dash.tick()
        self.reader.threads = [thread("t1", "Renamed")]
        self.reader.dirty = True
        self.assertTrue(self.dash.tick())
        self.assertIn("Renamed", self.dash.hub.html)

    def test_a_database_change_with_the_same_html_publishes_nothing(self):
        self.dash.tick()
        self.reader.dirty = True
        self.assertFalse(self.dash.tick())

    def test_a_plan_change_publishes_new_html(self):
        self.dash.tick()
        self.store.set_goal("t1", "Ship the map")
        self.assertTrue(self.dash.tick())
        self.assertIn("Ship the map", self.dash.hub.html)

    def test_the_app_renders_again_after_the_refresh_interval(self):
        self.dash.tick()
        self.reader.threads = [thread("t1", "Quietly renamed")]
        self.clock.t += timedelta(seconds=14)
        self.assertFalse(self.dash.tick())
        self.clock.t += timedelta(seconds=1)
        self.assertTrue(self.dash.tick())

    def test_new_cells_publish_new_html(self):
        cells = [CELLS]
        dash = Dashboard(self.reader, self.store, lambda: cells[0], "demo", clock=self.clock)
        dash.tick()
        cells[0] = [Cell("lib", "lib", 1, 0, 0, 1080, 600, 10)]
        self.assertTrue(dash.tick())
        self.assertIn('data-cell="lib"', dash.hub.html)


class HubTest(unittest.TestCase):
    def test_a_waiter_wakes_on_a_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, *_ = make_dashboard(tmp)
            dash.tick()
            version = dash.hub.version
            got = []
            waiter = threading.Thread(target=lambda: got.append(dash.hub.wait(version, 5)))
            waiter.start()
            time.sleep(0.05)
            dash.hub.publish("<main id=\"app\">new</main>")
            waiter.join(2)
            self.assertEqual(got[0], (version + 1, "<main id=\"app\">new</main>"))

    def test_a_wait_times_out_with_the_same_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, *_ = make_dashboard(tmp)
            dash.tick()
            self.assertEqual(dash.hub.wait(dash.hub.version, 0.05)[0], dash.hub.version)


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dash, self.reader, self.store, self.clock = make_dashboard(
            self.tmp.name, [thread("t1", "First")], ping_every=0.2)
        self.dash.tick()
        self.server = make_server(self.dash, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def mcp(self, payload, headers=None):
        h = {"Content-Type": "application/json", "X-T3-Thread": "t1"}
        h.update(headers or {})
        return self.request("POST", "/mcp", json.dumps(payload).encode(), h)

    def open_stream(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n")
        return sock

    def read_until(self, sock, marker, limit=5.0):
        buf, end = b"", time.monotonic() + limit
        while marker not in buf and time.monotonic() < end:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf

    def test_the_page_holds_the_current_app_and_opens_the_stream(self):
        resp, data = self.request("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertIn(b"First", data)
        self.assertIn(b"@get('/stream'", data)
        self.assertEqual(resp.getheader("Content-Length"), str(len(data)))

    def test_static_files_are_served_with_their_types(self):
        resp, data = self.request("GET", "/static/datastar.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.getheader("Content-Type"))
        self.assertGreater(len(data), 1000)
        resp, _ = self.request("GET", "/static/app.css")
        self.assertIn("text/css", resp.getheader("Content-Type"))

    def test_only_known_static_files_are_served(self):
        for path in ("/static/../server.py", "/static/plan.py", "/nothing"):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)

    def test_the_stream_sends_the_app_at_once(self):
        sock = self.open_stream()
        buf = self.read_until(sock, b"\n\n")
        sock.close()
        self.assertIn(b"200", buf.split(b"\r\n")[0])
        self.assertIn(b"Content-Type: text/event-stream", buf)
        self.assertIn(b"Cache-Control: no-cache", buf)
        self.assertIn(b"event: datastar-patch-elements\ndata: elements <main id=\"app\">", buf)
        self.assertIn(b"First", buf)

    def test_the_stream_pings_when_nothing_changes(self):
        sock = self.open_stream()
        buf = self.read_until(sock, b": ping\n\n", limit=3)
        sock.close()
        after_first_event = buf.split(b"\n\n", 1)[1]
        self.assertTrue(after_first_event.startswith(b": ping\n\n"), after_first_event[:40])

    def test_the_stream_sends_each_version_once(self):
        sock = self.open_stream()
        self.read_until(sock, b"\n\n")
        self.dash.hub.publish('<main id="app">second</main>')
        buf = self.read_until(sock, b": ping\n\n", limit=3)
        sock.close()
        self.assertEqual(buf.count(b"event: datastar-patch-elements"), 1)
        self.assertIn(b"second", buf)

    def test_closing_the_server_does_not_wait_for_open_streams(self):
        sock = self.open_stream()
        self.read_until(sock, b"\n\n")
        self.server.shutdown()
        closer = threading.Thread(target=self.server.server_close)
        closer.start()
        closer.join(2)
        sock.close()
        self.assertFalse(closer.is_alive())

    def test_errors_are_plain_text(self):
        resp, data = self.request("GET", "/nothing")
        self.assertEqual((resp.getheader("Content-Type"), data), ("text/plain", b"not found\n"))
        resp, data = self.request("GET", "/mcp")
        self.assertEqual((resp.getheader("Content-Type"), data), ("text/plain", b"POST only\n"))

    def test_mcp_responses_carry_one_json_content_type(self):
        resp, _ = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(resp.getheader("Content-Type"), "application/json")

    def test_a_goal_posted_through_mcp_reaches_the_stream(self):
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                self.dash.tick()
                self.dash.wake.wait(0.05)
                self.dash.wake.clear()

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        try:
            sock = self.open_stream()
            self.read_until(sock, b"\n\n")
            resp, data = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "set_goal",
                                              "arguments": {"goal": "Draw the map"}}})
            self.assertEqual(resp.status, 200)
            self.assertFalse(json.loads(data)["result"].get("isError"))
            buf = self.read_until(sock, b"Draw the map")
            sock.close()
            self.assertIn(b"Draw the map", buf)
        finally:
            stop.set()
            poller.join(2)

    def test_an_mcp_call_without_a_header_goes_to_the_thread_that_the_finder_names(self):
        self.dash.find_caller = lambda tool, arguments: "codex-thread"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/mcp", json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "set_goal", "arguments": {"goal": "From Codex"}}}).encode(),
            {"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        self.assertEqual(self.store.plan("codex-thread").goal, "From Codex")

    def test_mcp_initialize_answers_with_json(self):
        resp, data = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "1"}}})
        self.assertEqual(resp.status, 200)
        self.assertIn("application/json", resp.getheader("Content-Type"))
        self.assertEqual(json.loads(data)["result"]["protocolVersion"], "2025-06-18")

    def test_mcp_notifications_get_202(self):
        resp, data = self.mcp({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((resp.status, data), (202, b""))

    def test_mcp_rejects_a_foreign_origin(self):
        resp, data = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                              {"Origin": "http://evil.example"})
        self.assertEqual((resp.status, data), (403, b"origin not allowed\n"))

    def test_mcp_accepts_a_local_origin_on_its_own_port(self):
        for origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            resp, _ = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": origin})
            self.assertEqual(resp.status, 200, origin)
        resp, _ = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           {"Origin": "http://localhost:1"})
        self.assertEqual(resp.status, 403)

    def test_mcp_refuses_get(self):
        resp, _ = self.request("GET", "/mcp")
        self.assertEqual(resp.status, 405)
        self.assertEqual(resp.getheader("Allow"), "POST")

    def test_mcp_reads_a_body_of_exactly_one_mebibyte(self):
        body = b" " * (1024 * 1024 - 2) + b"{}"
        resp, _ = self.request("POST", "/mcp", body, {"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)

    def test_mcp_refuses_a_body_over_one_mebibyte(self):
        resp, data = self.request("POST", "/mcp", b"x" * (1024 * 1024 + 1),
                                  {"Content-Type": "application/json"})
        self.assertEqual((resp.status, data), (413, b"body too large\n"))

    def test_a_post_to_another_path_is_not_found(self):
        resp, data = self.request("POST", "/", b"{}")
        self.assertEqual((resp.status, data), (404, b"not found\n"))


class CellCacheTest(unittest.TestCase):
    def test_cells_are_empty_until_the_first_refresh_then_map_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "lib").mkdir()
            (Path(tmp) / "lib" / "a.py").write_text("x\ny\n")
            cache = CellCache(Path(tmp))
            self.assertEqual(cache(), [])
            cache.refresh()
            self.assertEqual([c.key for c in cache()], ["lib", "lib/."])
            self.assertEqual((cache()[0].w, cache()[0].h), (1080.0, 600.0))


class PageBeforeFirstTickTest(unittest.TestCase):
    def test_the_page_has_an_app_element_before_the_first_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, *_ = make_dashboard(tmp)
            server = make_server(dash, "127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                conn.request("GET", "/")
                self.assertIn(b'<main id="app"></main>', conn.getresponse().read())
                conn.close()
            finally:
                server.shutdown()
                server.server_close()


class PollerTest(unittest.TestCase):
    def test_the_poller_publishes_until_it_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, *_ = make_dashboard(tmp, [thread("t1")])
            stop = threading.Event()
            runner = threading.Thread(target=dash.run, args=(stop,))
            runner.start()
            dash.hub.wait(0, 5)
            stop.set()
            dash.wake.set()
            runner.join(2)
            self.assertFalse(runner.is_alive())
            self.assertEqual(dash.hub.version, 1)


class ClientGoneTest(unittest.TestCase):
    def test_a_client_that_closes_mid_body_does_not_hang_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, *_ = make_dashboard(tmp)
            server = make_server(dash, "127.0.0.1", 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5)
                sock.sendall(b"POST /mcp HTTP/1.0\r\nContent-Length: 2000000\r\n\r\nabc")
                sock.shutdown(socket.SHUT_WR)
                self.assertIn(b"413", sock.recv(100))
                sock.close()
            finally:
                server.shutdown()
                server.server_close()


class PollerTimingTest(unittest.TestCase):
    def test_the_poller_ticks_on_its_own_without_a_wake(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, reader, *_ = make_dashboard(tmp, [thread("t1", "One")])
            stop = threading.Event()
            runner = threading.Thread(target=dash.run, args=(stop,))
            runner.start()
            try:
                dash.hub.wait(0, 5)
                reader.threads = [thread("t1", "Two")]
                reader.dirty = True
                version, html = dash.hub.wait(1, 2)
                self.assertEqual(version, 2)
            finally:
                stop.set()
                dash.wake.set()
                runner.join(2)

    def test_the_snapshot_is_taken_at_the_clock_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash, reader, _, clock = make_dashboard(tmp)
            seen = []
            reader.snapshot = lambda now: seen.append(now) or []
            dash.tick()
            self.assertEqual(seen, [clock.t])


if __name__ == "__main__":
    unittest.main()
