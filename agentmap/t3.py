"""The T3 Code reader.

`T3Reader` reads T3 Code's database (`~/.t3/userdata/statev2.sqlite`)
read-only and builds one snapshot of the watched project's threads.

The model:

- A thread's liveness comes from its newest run. A subagent that Claude's
  Agent tool started has no runs. Its parent's `subagent` turn item
  (the item whose `node_id` is the thread's `forkedFrom.nodeId`) stands for
  its run.
- Only a working thread does something at this moment. Thus only a working
  thread can need the user or have a now line. Both come from the active
  turn items of its newest run. Active items left behind in a finished run
  are stale, and the reader ignores them.
- The thought, the last thing said and the touched paths come from all the
  turn items of the thread.

Cache: T3 Code moves a thread's `updated_at` whenever one of its turn items
or runs changes. The reader keeps the item-derived details of each thread
and reads the turn items again only when `updated_at` or the newest run
changed. The liveness query runs on every snapshot, because a native
subagent's liveness lives in its parent's turn items.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKING_RUN = frozenset({"queued", "preparing", "starting", "running", "waiting"})
ACTIVE_ITEM = ("pending", "running", "waiting")
REQUESTS = frozenset({"user_input_request", "approval_request", "secret_request"})
MAX_TOUCHED = 8
MAX_SENTENCE = 160
MAX_COMMAND = 100

_BASH_LC = re.compile(r"^(?:\S*/)?bash -lc\s+(.*)$", re.DOTALL)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_CLAUDE_WORKTREE = re.compile(r"^\.claude/worktrees/[^/]+/")


@dataclass(frozen=True)
class AgentThread:
    """One T3 Code thread as the dashboard shows it.

    `state` is one of working, needs_you, idle, failed. `touched` holds up to
    MAX_TOUCHED project-relative paths, newest first. In a snapshot, every
    `parent_id` names a thread of the same snapshot, or is None.
    """

    id: str
    parent_id: str | None
    title: str
    provider: str
    model: str | None
    state: str
    created_at: datetime
    updated_at: datetime
    now: str | None
    thought: str | None
    last_said: str | None
    touched: tuple[str, ...]


@dataclass(frozen=True)
class _Details:
    """What the reader derives from the turn items of one thread."""

    needs_you: bool
    now: str | None
    thought: str | None
    last_said: str | None
    touched: tuple[str, ...]
    touched_ordinal: int  # The newest ordinal that the touched scan has seen.


class T3Reader:
    """A read-only view of one project's threads in T3 Code's database.

    One connection serves all calls. A lock makes `thread_project` safe to call
    from other Python threads while the poller calls `snapshot`.
    """

    def __init__(self, db_path: Path, project_root: Path, window: timedelta):
        self._root = posixpath.normpath(str(Path(project_root).absolute()))
        self._window = window
        uri = Path(db_path).absolute().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False,
                                     isolation_level=None)
        self._lock = threading.Lock()
        self._data_version: int | None = None
        self._cache: dict[str, tuple[tuple, _Details]] = {}

    def close(self) -> None:
        self._conn.close()

    def changed(self) -> bool:
        """True if PRAGMA data_version moved since the last call, and on the first call."""
        with self._lock:
            version = self._conn.execute("PRAGMA data_version").fetchone()[0]
        moved = version != self._data_version
        self._data_version = version
        return moved

    def thread_project(self, thread_id: str) -> bool | None:
        """True for a thread of the watched project, False for another project, None if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT project_id FROM orchestration_v2_projection_threads WHERE thread_id = ?",
                (thread_id,)).fetchone()
            if row is None:
                return None
            return row[0] in self._project_ids()

    def snapshot(self, now: datetime) -> list[AgentThread]:
        """The project's threads updated inside the window, with all their ancestors."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                rows = self._thread_rows(_stamp(now - self._window))
                threads = [self._thread(row) for row in rows.values()]
            finally:
                self._conn.execute("COMMIT")
        self._cache = {k: v for k, v in self._cache.items() if k in rows}
        # A parent that is deleted or archived is not in the snapshot. Its child becomes a root.
        threads = [t if t.parent_id is None or t.parent_id in rows
                   else replace(t, parent_id=None) for t in threads]
        threads.sort(key=lambda t: (t.created_at, t.id))
        return threads

    # Queries. Each runs with self._lock held.

    def _project_ids(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT project_id FROM projection_projects"
            " WHERE workspace_root IN (?, ?) AND deleted_at IS NULL",
            (self._root, self._root + "/"))]

    def _thread_rows(self, cutoff: str) -> dict[str, tuple]:
        columns = ("SELECT thread_id, title, created_at, updated_at, payload_json"
                   " FROM orchestration_v2_projection_threads"
                   " WHERE deleted_at IS NULL AND archived_at IS NULL AND ")
        ids = self._project_ids()
        marks = ",".join("?" * len(ids))
        rows = {r[0]: r for r in self._conn.execute(
            columns + f"project_id IN ({marks}) AND updated_at >= ?", (*ids, cutoff))}
        missing = {_parent_of(r) for r in rows.values()} - rows.keys() - {None}
        while missing:
            marks = ",".join("?" * len(missing))
            found = {r[0]: r for r in self._conn.execute(
                columns + f"thread_id IN ({marks})", tuple(missing))}
            rows.update(found)
            missing = {_parent_of(r) for r in found.values()} - rows.keys() - {None}
        return rows

    def _thread(self, row: tuple) -> AgentThread:
        thread_id, title, created_at, updated_at, payload_json = row
        payload = json.loads(payload_json)
        run_id, liveness = self._liveness(thread_id, payload)
        key = (updated_at, run_id, liveness)
        cached = self._cache.get(thread_id)
        if cached is None or cached[0] != key:
            before = cached[1] if cached else None
            cached = (key, self._details(thread_id, payload, run_id, liveness == "working", before))
            self._cache[thread_id] = cached
        details = cached[1]
        state = "needs_you" if details.needs_you else liveness
        model = (payload.get("modelSelection") or {}).get("model")
        return AgentThread(
            id=thread_id, parent_id=_parent_of(row), title=title,
            provider=_provider(payload.get("providerInstanceId") or ""), model=model,
            state=state, created_at=_parse(created_at), updated_at=_parse(updated_at),
            now=details.now, thought=details.thought, last_said=details.last_said,
            touched=details.touched)

    def _liveness(self, thread_id: str, payload: dict) -> tuple[str | None, str]:
        """The newest run's id (None for a native subagent) and one of working, failed, idle."""
        run = self._conn.execute(
            "SELECT run_id, status FROM orchestration_v2_projection_runs"
            " WHERE thread_id = ? ORDER BY ordinal DESC LIMIT 1", (thread_id,)).fetchone()
        if run is not None:
            return run[0], _liveness_of(run[1], WORKING_RUN)
        node_id = (payload.get("forkedFrom") or {}).get("nodeId")
        if node_id is None:
            return None, "idle"
        item = self._conn.execute(
            "SELECT status FROM orchestration_v2_projection_turn_items"
            " WHERE node_id = ? AND type = 'subagent' ORDER BY ordinal DESC LIMIT 1",
            (node_id,)).fetchone()
        return None, _liveness_of(item[0] if item else "", ACTIVE_ITEM)

    def _details(self, thread_id: str, payload: dict, run_id: str | None, working: bool,
                 before: _Details | None) -> _Details:
        roots = [r for r in (payload.get("worktreePath"), self._root) if r]
        needs_you, now = False, None
        if working:
            # The status list is literal so that SQLite can use T3 Code's partial index of
            # active items. The run filter is in Python: `run_id IS ?` in SQL makes SQLite
            # pick the run index, which is slow for the many items with no run.
            active = [(t, s, p) for t, s, p, r in self._conn.execute(
                "SELECT type, status, payload_json, run_id"
                " FROM orchestration_v2_projection_turn_items"
                " WHERE thread_id = ? AND status IN ('pending', 'running', 'waiting')"
                " ORDER BY ordinal DESC", (thread_id,)) if r == run_id]
            needs_you = any(t in REQUESTS and s == "waiting" for t, s, _ in active)
            lines = (_now_line(t, json.loads(p), roots) for t, _, p in active)
            now = next((line for line in lines if line), None)
        touched, touched_ordinal = self._touched(
            thread_id, roots, before.touched if before else (),
            before.touched_ordinal if before else -1)
        return _Details(needs_you=needs_you, now=now,
                        thought=self._first_sentence_of_newest(thread_id, "reasoning"),
                        last_said=self._first_sentence_of_newest(thread_id, "assistant_message"),
                        touched=touched, touched_ordinal=touched_ordinal)

    def _first_sentence_of_newest(self, thread_id: str, type_: str) -> str | None:
        row = self._conn.execute(
            "SELECT json_extract(payload_json, '$.text') AS text"
            " FROM orchestration_v2_projection_turn_items"
            " WHERE thread_id = ? AND type = ?"
            " AND trim(coalesce(text, ''), ' ' || char(9, 10, 13)) != ''"
            " ORDER BY ordinal DESC LIMIT 1", (thread_id, type_)).fetchone()
        return _first_sentence(row[0]) if row else None

    def _touched(self, thread_id: str, roots: list[str], before: tuple[str, ...],
                 after: int) -> tuple[tuple[str, ...], int]:
        """The touched paths, and the newest ordinal seen.

        The scan reads only the items newer than `after`, and puts the paths that it
        finds in front of the paths `before` from the older items.
        """
        rows = self._conn.execute(
            "SELECT ordinal, coalesce(json_extract(payload_json, '$.fileName'),"
            " json_extract(payload_json, '$.input.file_path'),"
            " json_extract(payload_json, '$.input.path'),"
            " json_extract(payload_json, '$.input.notebook_path')) AS path"
            " FROM orchestration_v2_projection_turn_items"
            " WHERE thread_id = ? AND ordinal > ? AND type IN ('file_change', 'dynamic_tool')"
            " AND path IS NOT NULL ORDER BY ordinal DESC", (thread_id, after))
        touched: list[str] = []
        newest = after
        for ordinal, path in rows:
            newest = max(newest, ordinal)
            rel = _relative(path, roots)
            if rel is not None and rel not in touched:
                touched.append(rel)
                if len(touched) == MAX_TOUCHED:
                    break
        touched += [p for p in before if p not in touched]
        return tuple(touched[:MAX_TOUCHED]), newest


def _stamp(t: datetime) -> str:
    """T3 Code's text form of a time, for example 2026-11-14T14:50:02.179Z."""
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _parent_of(row: tuple) -> str | None:
    return (json.loads(row[4]).get("lineage") or {}).get("parentThreadId")


def _provider(instance_id: str) -> str:
    lowered = instance_id.lower()
    if "claude" in lowered:
        return "claude"
    if "codex" in lowered:
        return "codex"
    return instance_id


def _liveness_of(status: str, working: frozenset | tuple) -> str:
    if status in working:
        return "working"
    return "failed" if status == "failed" else "idle"


def _relative(path: str, roots: list[str]) -> str | None:
    """The path relative to the first root that holds it, or None."""
    if not path.startswith("/"):
        return None
    path = posixpath.normpath(path)
    for root in roots:
        root = posixpath.normpath(root)
        if path.startswith(root + "/"):
            return _CLAUDE_WORKTREE.sub("", path[len(root) + 1:])
    return None


def _short_tool_name(name: str) -> str:
    if name.startswith("mcp__"):
        return name.split("__", 2)[-1]
    return name.split(".", 1)[-1]


def _command(text: str) -> str:
    """The first line of a command without its `bash -lc` wrapper, at most MAX_COMMAND long."""
    wrapped = _BASH_LC.match(text)
    if wrapped:
        try:
            text = shlex.split(wrapped.group(1))[0]
        except (ValueError, IndexError):  # Unbalanced quotes, or no command.
            text = wrapped.group(1).lstrip("'\"")
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first if len(first) <= MAX_COMMAND else first[:MAX_COMMAND - 1] + "…"


def _now_line(type_: str, item: dict, roots: list[str]) -> str | None:
    """The now line of one active turn item, or None if its kind has no now line.

    An item that lacks the detail that its rule names gets a general line.
    """
    if type_ == "command_execution":
        command = _command(item.get("input") or "")
        return "Running: " + command if command else "Running a command"
    if type_ == "file_change":
        path = item.get("fileName")
        return "Editing " + (_relative(path, roots) or path) if path else "Editing a file"
    if type_ == "dynamic_tool":
        name = item.get("toolName")
        return _tool_line(name, item.get("input"), roots) if name else "Using a tool"
    if type_ == "web_search":
        patterns = item.get("patterns")
        return "Searching the web: " + patterns[0] if patterns else "Searching the web"
    if type_ == "reasoning":
        return "Thinking"
    if type_ == "assistant_message":
        return "Writing a reply"
    if type_ == "subagent":
        return "Waiting on " + (item.get("title") or "a subagent")
    if type_ in REQUESTS:
        return "Needs your answer"
    return None


def _tool_line(name: str, args: object, roots: list[str]) -> str:
    if not isinstance(args, dict):
        args = {}
    if name == "Read" and args.get("file_path"):
        path = args["file_path"]
        return "Reading " + (_relative(path, roots) or path)
    if name in ("Grep", "Glob") and args.get("pattern"):
        return "Searching " + args["pattern"]
    if name.endswith("delegate_task") and args.get("title"):
        return "Delegating: " + args["title"]
    if name.endswith(("background_wait", "t3_thread_wait")):
        return "Waiting on background work"
    return "Using " + _short_tool_name(name)


def _first_sentence(text: str) -> str | None:
    """The first sentence of the first non-empty line, at most MAX_SENTENCE characters."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return None
    end = _SENTENCE_END.search(line)
    sentence = line[:end.end()] if end else line
    if len(sentence) > MAX_SENTENCE:
        sentence = sentence[:MAX_SENTENCE - 1] + "…"
    return sentence
