"""HTTP server of the dashboard. See docs/plans/agentmap.md, section server.py.

One process serves three things on one port:

- the page and its two static files,
- `GET /stream`, a Server-Sent Events stream of Datastar `patch-elements` events,
- `POST /mcp`, the MCP endpoint through which agents post their status lines.

The poller thread is the only caller of the T3 reader's `snapshot` and of
the `app` component. It publishes each new app HTML to a `Hub`, and every open stream
waits on that hub.
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from agentmap import mcp
from agentmap.components import app, cell_peek, epic_strip, page, ticket_lists, ticket_peek
from agentmap.components.project_map.project_map import MAP_H, MAP_W
from agentmap.components.view import Forest, active_groups, ticket_agents
from agentmap.t3 import anchors
from agentmap.treemap import Cell, build_cells, code_weights

STATIC_DIR = Path(__file__).parent / "static"
STATIC = {
    "datastar.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
MAX_BODY = 1024 * 1024
POLL_SECONDS = 0.25
EMPTY = {
    "app": '<main id="app"></main>',
    "tickets": '<div id="tickets"></div>',
    "epics": '<section id="epics" class="mod"></section>',
}


def sse_patch(html: str, selector: str | None = None, mode: str | None = None) -> bytes:
    """One Datastar `patch-elements` event.

    Without a selector, it morphs the element with the same id. With a
    selector and the mode "inner", it morphs the children of that element.
    """
    lines = html.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    head = (f"data: selector {selector}\n" if selector else "") + (f"data: mode {mode}\n" if mode else "")
    data = "".join(f"data: elements {line}\n" for line in lines)
    return f"event: datastar-patch-elements\n{head}{data}\n".encode()


class Hub:
    """The newest HTML of each page fragment, with a version that grows by one on each change.

    The fragments are "app", "tickets" and "epics". A stream keeps what it
    sent and patches only the fragments that differ.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self.version = 0
        self.fragments: dict[str, str] = {}

    @property
    def html(self) -> str:
        """The app fragment."""
        return self.fragments.get("app", "")

    def publish(self, fragments: dict[str, str]) -> bool:
        """Store the fragments and wake all waiters. False if none of them changed."""
        with self._cond:
            merged = {**self.fragments, **fragments}
            if merged == self.fragments:
                return False
            self.fragments = merged
            self.version += 1
            self._cond.notify_all()
            return True

    def wait(self, version: int, timeout: float) -> tuple[int, dict[str, str]]:
        """Wait until the version differs from `version`, or until the timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self.version != version, timeout)
            return self.version, dict(self.fragments)


class Dashboard:
    """The state behind the server: the reader, the status store, the cells and the hub."""

    def __init__(self, reader, store, cells: Callable[[], list[Cell]], project_name: str, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 refresh: float = 15.0, ping_every: float = 15.0,
                 find_caller: Callable[[str, dict], str] | None = None, tickets=None):
        self.reader = reader
        self.tickets = tickets
        self.store = store
        self.cells = cells
        self.project_name = project_name
        self.clock = clock
        self.refresh = timedelta(seconds=refresh)
        self.ping_every = ping_every
        self.find_caller = find_caller
        self.hub = Hub()
        self.wake = threading.Event()
        self._last_render: datetime | None = None
        self._last_statuses = None
        self._last_cells = None
        self._last_tickets = None
        self._last_ticket_key = None
        # What the newest render drew: (cells, forest, events, now), for the boxes of map cells.
        self.view: tuple = ([], Forest([]), [], clock())

    def tick(self) -> bool:
        """Render again if anything changed or the refresh interval passed.

        Returns True if new HTML went to the hub.
        """
        now = self.clock()
        db_changed = self.reader.changed()
        statuses = self.store.statuses()
        cells = self.cells()
        tickets = self.tickets.tickets() if self.tickets is not None else {}
        due = self._last_render is None or now - self._last_render >= self.refresh
        if not (db_changed or due or statuses != self._last_statuses or cells != self._last_cells
                or tickets != self._last_tickets):
            return False
        # The event log merges T3 Code's history with the status log's history.
        events = self.reader.events(now) + self.store.events()
        threads = self.reader.snapshot(now)
        fragments = {"app": app(threads, statuses, cells, events, now, self.project_name, tickets.keys())}
        forest = Forest(threads)
        self.view = (cells, forest, events, now)
        links = ticket_agents(forest, statuses, tickets.keys())
        # The ticket fragments are large, so they render again only when the
        # tickets, the threads that work on them, or the ages may have changed.
        key = (links, {t: (forest.tag[t], forest.hue[t]) for ids in links.values() for t in ids})
        if tickets != self._last_tickets or key != self._last_ticket_key or due:
            found = active_groups(tickets, links)
            fragments["epics"] = epic_strip(found, tickets, links, forest, now)
            fragments["tickets"] = ticket_lists(found, tickets, links, forest, now)
        self._last_render, self._last_statuses, self._last_cells = now, statuses, cells
        self._last_tickets, self._last_ticket_key = tickets, key
        return self.hub.publish(fragments)

    def run(self, stop: threading.Event) -> None:
        """The poller loop. An MCP call sets `wake` for a render without delay."""
        while not stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - a bad tick must not stop the dashboard
                traceback.print_exc()
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()


class CellCache:
    """The project's files, listed again every `every` seconds.

    It keeps two views of the list: the treemap cells, and the anchors that
    T3Reader uses to find the project inside other copies of it.
    """

    def __init__(self, root: Path, every: float = 600.0):
        self.root = root
        self.every = every
        self.cells: list[Cell] = []
        self._anchors: frozenset[str] = frozenset()
        # The weight of each file by size and time, so that a refresh reads only changed files.
        self._weights: dict = {}

    def __call__(self) -> list[Cell]:
        return self.cells

    def anchors(self) -> frozenset[str]:
        return self._anchors

    def refresh(self) -> None:
        weights = code_weights(self.root, self._weights)
        self.cells = build_cells(weights, MAP_W, MAP_H)
        self._anchors = anchors(weights)

    def run(self, stop: threading.Event) -> None:  # pragma: no cover - a timer loop
        while True:
            try:
                self.refresh()
            except Exception:
                traceback.print_exc()
            if stop.wait(self.every):
                return


class _Handler(BaseHTTPRequestHandler):
    server_version = "agentmap"
    dashboard: Dashboard  # set by make_server

    def log_message(self, format, *args):  # pragma: no cover - quiet access log
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            f = {**EMPTY, **self.dashboard.hub.fragments}
            self._send(200, page(f["app"], f["epics"], f["tickets"]).encode(),
                       {"Content-Type": "text/html; charset=utf-8"})
        elif path.startswith("/peek/"):
            self._box(path[len("/peek/"):], pinned=False)
        elif path.startswith("/pin/"):
            self._box(path[len("/pin/"):], pinned=True)
        elif path == "/stream":
            self._stream()
        elif path.startswith("/static/") and path[len("/static/"):] in STATIC:
            name = path[len("/static/"):]
            self._send(200, (STATIC_DIR / name).read_bytes(), {"Content-Type": STATIC[name]})
        elif path == "/mcp":
            self._error(405, "POST only", {"Allow": "POST"})
        else:
            self._error(404, "not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if urlsplit(self.path).path != "/mcp":
            self._discard(length)
            self._error(404, "not found")
            return
        if length > MAX_BODY:
            self._discard(length)
            self._error(413, "body too large")
            return
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin is not None and origin not in (f"http://127.0.0.1:{port}",
                                                 f"http://localhost:{port}"):
            self._discard(length)
            self._error(403, "origin not allowed")
            return
        body = self.rfile.read(length)
        status, headers, out = mcp.handle(body, self.headers, self.dashboard.store,
                                          self.dashboard.reader.thread_project,
                                          self.dashboard.find_caller)
        self._send(status, out, headers)
        self.dashboard.wake.set()

    def _discard(self, length: int) -> None:
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                return
            length -= len(chunk)

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, text: str, extra: dict[str, str] | None = None) -> None:
        self._send(status, f"{text}\n".encode(), {"Content-Type": "text/plain", **(extra or {})})

    def _box(self, subject: str, pinned: bool) -> None:
        """The box of a subject `<kind>/<quoted id>`, as an inner patch of #peek.

        The kind `t` is a ticket: its hover box, or its pinned box with the notes.
        The kind `c` is a map cell of the newest render: its edited files.
        """
        kind, _, raw = subject.partition("/")
        key = unquote(raw)
        html = None
        if kind == "t" and self.dashboard.tickets is not None:
            found = self.dashboard.tickets.tickets().get(key)
            html = ticket_peek(found, pinned) if found is not None else None
        elif kind == "c":
            cells, forest, events, now = self.dashboard.view
            if any(c.key == key for c in cells):
                html = cell_peek(key, cells, forest, events, now, pinned)
        if html is None:
            self._error(404, "no such box")
            return
        self._send(200, sse_patch(html, selector="#peek", mode="inner"),
                   {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})

    def _stream(self) -> None:
        hub = self.dashboard.hub
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        version, sent = hub.version, {**EMPTY, **hub.fragments}
        try:
            # The page order: a fragment patches the element with its id.
            self.wfile.write(b"".join(sse_patch(html) for html in sent.values()))
            self.wfile.flush()
            while True:
                new, fragments = hub.wait(version, self.dashboard.ping_every)
                changed = [html for name, html in fragments.items() if sent.get(name) != html]
                self.wfile.write(b"".join(sse_patch(html) for html in changed) or b": ping\n\n")
                self.wfile.flush()
                version = new
                sent.update(fragments)
        except OSError:
            return  # the page closed the stream


def make_server(dashboard: Dashboard, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"dashboard": dashboard})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
