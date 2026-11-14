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
  turn items of the thread. Each carries the start time of the item it came
  from.
- Events (see `events`) are dated facts inside the window: a thread started,
  a file edit, a request to the user, the end of a run.

Cache: T3 Code moves a thread's `updated_at` whenever one of its turn items
or runs changes. The reader keeps the item-derived details of each thread
and reads the turn items again only when `updated_at` or the newest run
changed. The liveness query runs on every snapshot, because a native
subagent's liveness lives in its parent's turn items. `events` keeps its own
cache per thread with the same key rule, and also reads the end of a native
subagent on every call.

Both item scans (touched paths and events) read only the items with an
ordinal above the newest ordinal that they saw. T3 Code appends items with
growing ordinals, and writes an item's path, question and start time when it
creates the item.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sqlite3
import threading
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmap.events import KINDS, Event

WORKING_RUN = frozenset({"queued", "preparing", "starting", "running", "waiting"})
ACTIVE_ITEM = ("pending", "running", "waiting")
REQUESTS = frozenset({"user_input_request", "approval_request", "secret_request"})
MAX_TOUCHED = 8
MAX_SENTENCE = 160
MAX_COMMAND = 100
MAX_EVENTS = 80
# The status of an ended run or subagent item, and the kind of its event.
END_KIND = {"completed": "finished", "failed": "failed"}

_BASH_LC = re.compile(r"^(?:\S*/)?bash -lc\s+(.*)$", re.DOTALL)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_CLAUDE_WORKTREE = re.compile(r"^\.claude/worktrees/[^/]+/")


@dataclass(frozen=True)
class Touch:
    """A touched path, and the start time (aware UTC) of the newest item that touched it."""

    path: str
    at: datetime


@dataclass(frozen=True)
class AgentThread:
    """One T3 Code thread as the dashboard shows it.

    `state` is one of working, needs_you, idle, failed. `touched` holds up to
    MAX_TOUCHED project-relative paths, newest first, without repeats.
    `thought_at` and `said_at` are the start times of the items that `thought`
    and `last_said` came from, or None when those are None. In a snapshot,
    every `parent_id` names a thread of the same snapshot, or is None.
    `delegation` is the status of the newest task that a parent gave to the
    thread (running, completed, failed, cancelled, interrupted), or None for a
    thread that got no task.
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
    thought_at: datetime | None
    said_at: datetime | None
    touched: tuple[Touch, ...]
    delegation: str | None = None


@dataclass(frozen=True)
class _Places:
    """What `_relative` needs to turn an absolute path into a project path.

    `roots` are checkouts that the reader knows: the thread's worktree and the
    project root. `anchors` are the first two parts of the project's file paths,
    which find the project inside any other copy of it.
    """

    roots: tuple[str, ...]
    anchors: Collection[str]


@dataclass(frozen=True)
class _Details:
    """What the reader derives from the turn items of one thread."""

    needs_you: bool
    now: str | None
    thought: tuple[str, datetime] | None
    said: tuple[str, datetime] | None
    touched: tuple[Touch, ...]
    touched_ordinal: int  # The newest ordinal that the touched scan has seen.


@dataclass(frozen=True)
class _Log:
    """What `events` keeps of one thread between calls."""

    updated_at: str  # The thread's updated_at when the log was read.
    ordinal: int  # The newest ordinal that the item scan has seen.
    items: tuple[Event, ...]  # Edits and asks at or after the window start of their scan.
    runs: tuple[Event, ...]  # Ends of runs inside the window.
    node_id: str | None  # The subagent item's node, for a thread without runs.
    said: str  # The first sentence of the newest message, for a thread without runs.


# The MCP server name that the harness configurations give agentmap.
SERVER_NAME = "agentmap"
# A tool call that started longer ago than this is stale.
CALL_MAX_AGE = timedelta(minutes=5)


class T3Reader:
    """A read-only view of one project's threads in T3 Code's database.

    One connection serves all calls. A lock makes `thread_project` safe to call
    from other Python threads while the poller calls `snapshot`.
    """

    def __init__(self, db_path: Path, project_root: Path, window: timedelta, *,
                 anchors: Callable[[], Collection[str]] = frozenset):
        self._root = posixpath.normpath(str(Path(project_root).absolute()))
        self._anchors = anchors
        self._window = window
        uri = Path(db_path).absolute().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False,
                                     isolation_level=None)
        self._lock = threading.Lock()
        self._data_version: int | None = None
        self._cache: dict[str, tuple[tuple, _Details]] = {}
        self._logs: dict[str, _Log] = {}
        # The roots of other checkouts that touched paths came from; see checkouts().
        self._checkouts: set[str] = set()

    def close(self) -> None:
        self._conn.close()

    def _places(self, payload: dict) -> _Places:
        """Where a path of this thread can be: its worktree, the project root, other copies."""
        roots = tuple(r for r in (payload.get("worktreePath"), self._root) if r)
        return _Places(roots, self._anchors())

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

    def checkouts(self) -> list[str]:
        """The roots of the other checkouts of the project that threads touched, sorted.

        Another checkout is a copy of the project outside the known roots, such as
        a scratch clone. Its root is the part of a touched path before the path's
        first anchor. Only roots that still exist on disk count.
        """
        with self._lock:
            found = list(self._checkouts)
        return sorted(r for r in found if os.path.isdir(r))

    def worktrees(self) -> list[str]:
        """The worktree folders of the project's threads that exist on disk, sorted.

        Every thread of the project that is not deleted counts, whatever its age,
        archived threads too: an archived thread's worktree can still hold tickets.
        """
        with self._lock:
            ids = self._project_ids()
            marks = ",".join("?" * len(ids))
            paths = [r[0] for r in self._conn.execute(
                "SELECT DISTINCT json_extract(payload_json, '$.worktreePath') AS path"
                " FROM orchestration_v2_projection_threads"
                f" WHERE deleted_at IS NULL AND project_id IN ({marks})", ids)]
        return sorted(p for p in paths if isinstance(p, str) and os.path.isdir(p))

    def find_tool_calls(self, tool: str, arguments: dict, now: datetime) -> list[tuple[str, str]]:
        """The active calls of the agentmap tool `tool` with exactly these arguments.

        Returns (turn item id, thread id) pairs, oldest first. Claude Code names
        the tool `mcp__agentmap__<tool>` and Codex names it `agentmap.<tool>`.
        A call that started more than CALL_MAX_AGE before `now` is stale and
        left out.
        """
        names = {f"mcp__{SERVER_NAME}__{tool}", f"{SERVER_NAME}.{tool}"}
        oldest = now - CALL_MAX_AGE
        with self._lock:
            rows = self._conn.execute(
                "SELECT turn_item_id, thread_id, payload_json"
                " FROM orchestration_v2_projection_turn_items"
                " WHERE type = 'dynamic_tool' AND status IN ('pending', 'running', 'waiting')"
            ).fetchall()
        found = []
        for item_id, thread_id, payload_json in rows:
            payload = json.loads(payload_json)
            started = payload.get("startedAt")
            if (payload.get("toolName") in names and started
                    and (payload.get("input") or {}) == arguments
                    and _parse(started) >= oldest):
                found.append((_parse(started), item_id, thread_id))
        return [(item_id, thread_id) for _, item_id, thread_id in sorted(found)]

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

    def events(self, now: datetime) -> list[Event]:
        """What the snapshot's threads did inside the window up to `now`, newest first.

        Gives at most MAX_EVENTS events. `now` must not move back between calls,
        because the cache keeps only the items inside the window of its scan.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        start = now - self._window
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                rows = self._thread_rows(_stamp(start))
                found = [event for row in rows.values() for event in self._events(row, start)]
            finally:
                self._conn.execute("COMMIT")
        self._logs = {k: v for k, v in self._logs.items() if k in rows}
        found = [e for e in found if start <= e.at <= now]
        found.sort(key=lambda e: (e.at, e.thread_id, KINDS.index(e.kind), e.text), reverse=True)
        return found[:MAX_EVENTS]

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
            now=details.now, thought=_text_of(details.thought),
            last_said=_text_of(details.said), thought_at=_time_of(details.thought),
            said_at=_time_of(details.said), touched=details.touched,
            delegation=self._delegation(thread_id))

    def _delegation(self, thread_id: str) -> str | None:
        """The status of the newest delegation record whose child is this thread.

        The query is not cached: a parent can give the thread a new task without
        a change to the thread's own row.
        """
        row = self._conn.execute(
            "SELECT status FROM orchestration_v2_projection_subagents"
            " WHERE child_thread_id = ?"
            " ORDER BY coalesce(started_at, updated_at) DESC LIMIT 1", (thread_id,)).fetchone()
        return row[0] if row else None

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
        places = self._places(payload)
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
            lines = (_now_line(t, json.loads(p), places) for t, _, p in active)
            now = next((line for line in lines if line), None)
        touched, touched_ordinal = self._touched(
            thread_id, places, before.touched if before else (),
            before.touched_ordinal if before else -1)
        return _Details(needs_you=needs_you, now=now,
                        thought=self._newest_sentence("thread_id", thread_id, "reasoning"),
                        said=self._newest_sentence("thread_id", thread_id, "assistant_message"),
                        touched=touched, touched_ordinal=touched_ordinal)

    def _newest_sentence(self, column: str, value: str,
                         type_: str) -> tuple[str, datetime] | None:
        """The first sentence of the newest non-blank text of this type, and its item's start.

        `column` is thread_id or run_id: the items of one thread or of one run.
        """
        row = self._conn.execute(
            "SELECT json_extract(payload_json, '$.text') AS text,"
            " json_extract(payload_json, '$.startedAt')"
            " FROM orchestration_v2_projection_turn_items"
            f" WHERE {column} = ? AND type = ?"
            " AND trim(coalesce(text, ''), ' ' || char(9, 10, 13)) != ''"
            " ORDER BY ordinal DESC LIMIT 1", (value, type_)).fetchone()
        sentence = _first_sentence(row[0]) if row else None
        return (sentence, _parse(row[1])) if sentence else None

    def _touched(self, thread_id: str, places: _Places, before: tuple[Touch, ...],
                 after: int) -> tuple[tuple[Touch, ...], int]:
        """The touched paths, and the newest ordinal seen.

        The scan reads only the items newer than `after`, and puts the paths that it
        finds in front of the paths `before` from the older items.
        """
        rows = self._conn.execute(
            "SELECT ordinal, coalesce(json_extract(payload_json, '$.fileName'),"
            " json_extract(payload_json, '$.input.file_path'),"
            " json_extract(payload_json, '$.input.path'),"
            " json_extract(payload_json, '$.input.notebook_path')) AS path,"
            " json_extract(payload_json, '$.startedAt')"
            " FROM orchestration_v2_projection_turn_items"
            " WHERE thread_id = ? AND ordinal > ? AND type IN ('file_change', 'dynamic_tool')"
            " AND path IS NOT NULL ORDER BY ordinal DESC", (thread_id, after))
        touched: list[Touch] = []
        seen: set[str] = set()
        newest = after
        for ordinal, path, started in rows:
            newest = max(newest, ordinal)
            rel = _relative(path, places)
            checkout = _checkout(path, places)
            if checkout is not None:
                self._checkouts.add(checkout)
            if rel is not None and rel not in seen:
                seen.add(rel)
                touched.append(Touch(rel, _parse(started)))
                if len(touched) == MAX_TOUCHED:
                    break
        touched += [t for t in before if t.path not in seen]
        return tuple(touched[:MAX_TOUCHED]), newest

    def _events(self, row: tuple, start: datetime) -> list[Event]:
        """The events of one thread: those of its cached log, its start and a subagent's end."""
        thread_id, title, created_at, updated_at, payload_json = row
        payload = json.loads(payload_json)
        log = self._logs.get(thread_id)
        if log is None or log.updated_at != updated_at:
            log = self._read_log(thread_id, payload, updated_at, start, log)
            self._logs[thread_id] = log
        events = [Event(_parse(created_at), thread_id, "started", title), *log.items, *log.runs]
        if log.node_id is not None:
            item = self._conn.execute(
                "SELECT status, json_extract(payload_json, '$.completedAt')"
                " FROM orchestration_v2_projection_turn_items"
                " WHERE node_id = ? AND type = 'subagent' ORDER BY ordinal DESC LIMIT 1",
                (log.node_id,)).fetchone()
            if item and item[0] in END_KIND and item[1]:
                events.append(Event(_parse(item[1]), thread_id, END_KIND[item[0]], log.said))
        return events

    def _read_log(self, thread_id: str, payload: dict, updated_at: str, start: datetime,
                  before: _Log | None) -> _Log:
        """The log of one thread, from the items newer than those that `before` saw."""
        places = self._places(payload)
        newest = before.ordinal if before else -1
        items: list[Event] = []
        for ordinal, type_, item_json in self._conn.execute(
                "SELECT ordinal, type, payload_json FROM orchestration_v2_projection_turn_items"
                " WHERE thread_id = ? AND ordinal > ? AND type IN ('file_change',"
                " 'user_input_request', 'approval_request', 'secret_request')"
                " ORDER BY ordinal DESC", (thread_id, newest)):
            newest = max(newest, ordinal)
            item = json.loads(item_json)
            at = _parse(item["startedAt"])
            if at < start:
                continue
            if type_ in REQUESTS:
                items.append(Event(at, thread_id, "asks", _ask(type_, item)))
                continue
            rel = _relative(item.get("fileName") or "", places)
            if rel is not None:
                items.append(Event(at, thread_id, "edits", rel))
        items += [e for e in (before.items if before else ()) if e.at >= start]
        runs = self._conn.execute(
            "SELECT run_id, status, completed_at FROM orchestration_v2_projection_runs"
            " WHERE thread_id = ? AND status IN ('completed', 'failed') AND completed_at >= ?",
            (thread_id, _stamp(start))).fetchall()
        ends = tuple(Event(_parse(completed_at), thread_id, END_KIND[status],
                           _text_of(self._newest_sentence("run_id", run_id, "assistant_message"))
                           or "")
                     for run_id, status, completed_at in runs)
        has_run = runs or self._conn.execute(
            "SELECT 1 FROM orchestration_v2_projection_runs WHERE thread_id = ? LIMIT 1",
            (thread_id,)).fetchone()
        node_id = None if has_run else (payload.get("forkedFrom") or {}).get("nodeId")
        said = (_text_of(self._newest_sentence("thread_id", thread_id, "assistant_message"))
                if node_id else None)
        return _Log(updated_at=updated_at, ordinal=newest, items=tuple(items), runs=ends,
                    node_id=node_id, said=said or "")


def _stamp(t: datetime) -> str:
    """T3 Code's text form of a time, for example 2026-11-14T14:50:02.179Z."""
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _text_of(found: tuple[str, datetime] | None) -> str | None:
    return found[0] if found else None


def _time_of(found: tuple[str, datetime] | None) -> datetime | None:
    return found[1] if found else None


def _ask(type_: str, item: dict) -> str:
    """The first sentence of what a request asks, or "".

    A question gives its first question, an approval its prompt, a secret its label.
    """
    if type_ == "user_input_request":
        questions = item.get("questions") or [{}]
        text = questions[0].get("question")
    elif type_ == "approval_request":
        text = item.get("prompt")
    else:
        text = item.get("label")
    return (_first_sentence(text) if isinstance(text, str) else None) or ""


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


def anchors(paths: Iterable[str]) -> frozenset[str]:
    """The first two parts of each project-relative path that has two or more parts."""
    return frozenset("/".join(p.split("/")[:2]) for p in paths if "/" in p)


def _relative(path: str, places: _Places) -> str | None:
    """The path in the project, or None.

    A path under a root is relative to the first root that holds it. Any other
    absolute path is taken to be in another copy of the project, such as a
    scratch clone or another worktree: it starts at its first pair of parts,
    from the left, that is an anchor.
    """
    split = _split(path, places)
    return split[1] if split else None


def _checkout(path: str, places: _Places) -> str | None:
    """The root of the other checkout that holds the path, or None for a path under a root."""
    split = _split(path, places)
    return split[0] if split else None


def _split(path: str, places: _Places) -> tuple[str | None, str] | None:
    """(the root of another checkout or None, the path in the project), or None."""
    if not path.startswith("/"):
        return None
    path = posixpath.normpath(path)
    for root in places.roots:
        root = posixpath.normpath(root)
        if path.startswith(root + "/"):
            return None, _CLAUDE_WORKTREE.sub("", path[len(root) + 1:])
    parts = path.split("/")[1:]
    for i in range(len(parts) - 1):
        if f"{parts[i]}/{parts[i + 1]}" in places.anchors:
            return ("/" + "/".join(parts[:i]) if i else None), "/".join(parts[i:])
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


def _now_line(type_: str, item: dict, places: _Places) -> str | None:
    """The now line of one active turn item, or None if its kind has no now line.

    An item that lacks the detail that its rule names gets a general line.
    """
    if type_ == "command_execution":
        command = _command(item.get("input") or "")
        return "Running: " + command if command else "Running a command"
    if type_ == "file_change":
        path = item.get("fileName")
        return "Editing " + (_relative(path, places) or path) if path else "Editing a file"
    if type_ == "dynamic_tool":
        name = item.get("toolName")
        return _tool_line(name, item.get("input"), places) if name else "Using a tool"
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


def _tool_line(name: str, args: object, places: _Places) -> str:
    if not isinstance(args, dict):
        args = {}
    if name == "Read" and args.get("file_path"):
        path = args["file_path"]
        return "Reading " + (_relative(path, places) or path)
    if name in ("Grep", "Glob") and args.get("pattern"):
        return "Searching " + args["pattern"]
    if name.endswith("delegate_task") and args.get("title"):
        return "Delegating: " + args["title"]
    if name.endswith(("background_wait", "t3_thread_wait")):
        return "Waiting on background work"
    return "Using " + _short_tool_name(name)


# Whole `<tag>...</tag>` blocks at the start of a text. The harness puts one
# there, for example `<tools-used-before-summary>Edit</tools-used-before-summary>`.
_LEADING_TAG_BLOCKS = re.compile(r"\A(?:\s*<([A-Za-z][\w-]*)>.*?</\1>)+", re.S)


def _first_sentence(text: str) -> str | None:
    """The first sentence of the first non-empty line, at most MAX_SENTENCE characters.

    Tag blocks at the start of the text are skipped.
    """
    text = _LEADING_TAG_BLOCKS.sub("", text, count=1)
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        return None
    end = _SENTENCE_END.search(line)
    sentence = line[:end.end()] if end else line
    if len(sentence) > MAX_SENTENCE:
        sentence = sentence[:MAX_SENTENCE - 1] + "…"
    return sentence
