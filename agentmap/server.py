"""HTTP server of the dashboard. See docs/plans/agentmap.md, section server.py.

One process serves three things on one port:

- the page and its two static files,
- `GET /stream`, a Server-Sent Events stream of Datastar `patch-elements` events,
- `POST /mcp`, the MCP endpoint through which agents post their plans.

The poller thread is the only caller of the T3 reader's `snapshot` and of
`render_app`. It publishes each new app HTML to a `Hub`, and every open stream
waits on that hub.
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from agentmap import mcp
from agentmap.render import MAP_H, MAP_W, render_app, render_page
from agentmap.treemap import Cell, build_cells, code_weights

STATIC_DIR = Path(__file__).parent / "static"
STATIC = {
    "datastar.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
MAX_BODY = 1024 * 1024
POLL_SECONDS = 0.25
EMPTY_APP = '<main id="app"></main>'


def sse_patch(html: str) -> bytes:
    """One Datastar `patch-elements` event that replaces the element with the same id."""
    lines = html.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    data = "".join(f"data: elements {line}\n" for line in lines)
    return f"event: datastar-patch-elements\n{data}\n".encode()


class Hub:
    """The newest app HTML, with a version that grows by one on each change."""

    def __init__(self):
        self._cond = threading.Condition()
        self.version = 0
        self.html = ""

    def publish(self, html: str) -> bool:
        """Store the HTML and wake all waiters. False if the HTML did not change."""
        with self._cond:
            if html == self.html:
                return False
            self.html = html
            self.version += 1
            self._cond.notify_all()
            return True

    def wait(self, version: int, timeout: float) -> tuple[int, str]:
        """Wait until the version differs from `version`, or until the timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self.version != version, timeout)
            return self.version, self.html


class Dashboard:
    """The state behind the server: the reader, the plan store, the cells and the hub."""

    def __init__(self, reader, store, cells: Callable[[], list[Cell]], project_name: str, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 refresh: float = 15.0, ping_every: float = 15.0,
                 find_caller: Callable[[str, dict], str] | None = None):
        self.reader = reader
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
        self._last_plans = None
        self._last_cells = None

    def tick(self) -> bool:
        """Render again if anything changed or the refresh interval passed.

        Returns True if new HTML went to the hub.
        """
        now = self.clock()
        db_changed = self.reader.changed()
        plans = self.store.plans()
        cells = self.cells()
        due = self._last_render is None or now - self._last_render >= self.refresh
        if not (db_changed or due or plans != self._last_plans or cells != self._last_cells):
            return False
        html = render_app(self.reader.snapshot(now), plans, cells, now, self.project_name)
        self._last_render, self._last_plans, self._last_cells = now, plans, cells
        return self.hub.publish(html)

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
    """The treemap cells of the project, built again every `every` seconds."""

    def __init__(self, root: Path, every: float = 600.0):
        self.root = root
        self.every = every
        self.cells: list[Cell] = []

    def __call__(self) -> list[Cell]:
        return self.cells

    def refresh(self) -> None:
        self.cells = build_cells(code_weights(self.root), MAP_W, MAP_H)

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
            self._send(200, render_page(self.dashboard.hub.html or EMPTY_APP).encode(),
                       {"Content-Type": "text/html; charset=utf-8"})
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

    def _stream(self) -> None:
        hub = self.dashboard.hub
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        version, html = hub.version, hub.html
        try:
            self.wfile.write(sse_patch(html or EMPTY_APP))
            self.wfile.flush()
            while True:
                new, html = hub.wait(version, self.dashboard.ping_every)
                self.wfile.write(sse_patch(html) if new != version else b": ping\n\n")
                self.wfile.flush()
                version = new
        except OSError:
            return  # the page closed the stream


def make_server(dashboard: Dashboard, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"dashboard": dashboard})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
