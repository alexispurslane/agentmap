"""Start the dashboard: python3 -m agentmap --project PATH."""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmap.caller import CallerFinder
from agentmap.server import CellCache, Dashboard, make_server
from agentmap.status import StatusStore
from agentmap.t3 import T3Reader

DEFAULT_DB = Path.home() / ".t3" / "userdata" / "statev2.sqlite"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agentmap", description=__doc__)
    p.add_argument("--project", type=Path, required=True,
                   help="root of the T3 Code project to watch")
    p.add_argument("--port", type=int, default=7788, help="port on 127.0.0.1 (default 7788)")
    p.add_argument("--t3-db", type=Path, default=DEFAULT_DB,
                   help=f"T3 Code state database (default {DEFAULT_DB})")
    p.add_argument("--hours", type=float, default=3.0,
                   help="show threads active in the last N hours (default 3)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = args.project.expanduser().resolve()
    if not root.is_dir():
        print(f"agentmap: {root} is not a directory", file=sys.stderr)
        return 2
    if not args.t3_db.exists():
        print(f"agentmap: T3 Code database not found at {args.t3_db}", file=sys.stderr)
        return 2
    cells = CellCache(root)
    # The reader keeps what it reads from old items, so the anchors must be in
    # place before its first scan: a path that it drops then stays dropped.
    cells.refresh()
    reader = T3Reader(args.t3_db, root, timedelta(hours=args.hours), anchors=cells.anchors)
    store = StatusStore(root / ".agentmap" / "status.jsonl")
    # Codex threads share one process and send no thread header. The finder
    # names their thread from T3 Code's record of the call.
    find_caller = CallerFinder(
        lambda tool, arguments: reader.find_tool_calls(tool, arguments, datetime.now(timezone.utc)))
    dashboard = Dashboard(reader, store, cells, root.name, find_caller=find_caller)
    server = make_server(dashboard, "127.0.0.1", args.port)
    stop = threading.Event()
    for target in (cells.run, dashboard.run):
        threading.Thread(target=target, args=(stop,), daemon=True).start()
    port = server.server_address[1]
    print(f"agentmap: watching {root}\n"
          f"  dashboard  http://127.0.0.1:{port}/\n"
          f"  MCP        http://127.0.0.1:{port}/mcp", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
