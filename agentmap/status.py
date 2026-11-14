"""The status store: the one-line status that each agent thread posts through MCP.

The source of truth is an append-only log with one JSON object per line:
`{"ts": "2026-11-14T15:00:00Z", "thread": "...", "op": "status", "text": "..."}`.
The statuses in memory are the fold of that log. The log of the old plan
store had other records ("goal", "add", "mark"); a replay skips them.

Invariants, which `StatusStore.check_invariants` tests in every build:

1. A fresh replay of the log gives the same statuses as the memory.
2. A fresh replay of the log gives the same events as the memory.

Times come from the `ts` of a log record, so a replay gives the same times.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from agentmap.events import Event

STATUS_MAX = 160
# events() gives at most this many events.
EVENTS_MAX = 200
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class StatusError(ValueError):
    """A refused status. The message is shown to the agent."""


@dataclass(frozen=True)
class Status:
    text: str
    at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StatusStore:
    def __init__(self, log_path: pathlib.Path):
        self._path = pathlib.Path(log_path)
        self._lock = threading.Lock()
        self._statuses, self._events = _replay(self._path, warn=True)

    def set_status(self, thread: str, text: str) -> None:
        """Post the thread's status. Raise StatusError for a refused status."""
        record = {"ts": _now().strftime(_TS_FORMAT), **_normalize(thread, text)}
        with self._lock:
            self._append(record)
            _fold(self._statuses, self._events, record)

    def status(self, thread: str) -> Status | None:
        with self._lock:
            return self._statuses.get(thread)

    def statuses(self) -> dict[str, Status]:
        with self._lock:
            return dict(self._statuses)

    def events(self) -> list[Event]:
        """The newest EVENTS_MAX events of all threads, newest first."""
        with self._lock:
            return list(reversed(self._events))

    def check_invariants(self) -> None:
        """Raise AssertionError when an invariant of the module docstring is broken."""
        with self._lock:
            statuses, events = dict(self._statuses), list(self._events)
            replayed, replayed_events = _replay(self._path, warn=False)
        problems = []
        if replayed != statuses:
            problems.append("a fresh replay of the log differs from the memory")
        if list(replayed_events) != events:
            problems.append("the events of a fresh replay of the log differ from the memory")
        if problems:
            raise AssertionError("; ".join(problems))

    def _append(self, record: dict) -> None:
        assert self._lock.locked()
        line = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a+b") as log:
            if log.seek(0, 2) > 0:
                log.seek(-1, 2)
                if log.read(1) != b"\n":
                    # The last line was torn, for example by a crash. Do not
                    # join this record to it.
                    line = b"\n" + line
            log.write(line)
            log.flush()


def _normalize(thread, text) -> dict:
    if not isinstance(thread, str) or not thread:
        raise StatusError("The thread id is empty.")
    if not isinstance(text, str):
        raise StatusError("The status line must be a string.")
    text = text.strip()
    if not text:
        raise StatusError("The status line is empty.")
    if len(text) > STATUS_MAX:
        raise StatusError(f"The status line has {len(text)} characters; the limit is {STATUS_MAX}.")
    return {"thread": thread, "op": "status", "text": text}


def _fold(statuses: dict[str, Status], events: deque[Event], record: dict) -> None:
    """Apply one checked status record to the statuses and the events."""
    at = datetime.strptime(record["ts"], _TS_FORMAT).replace(tzinfo=timezone.utc)
    thread, text = record["thread"], record["text"]
    old = statuses.get(thread)
    statuses[thread] = Status(text, at)
    if old is None or old.text != text:
        events.append(Event(at, thread, "status", text))


def _replay(path: pathlib.Path, warn: bool) -> tuple[dict[str, Status], deque[Event]]:
    """The statuses and the newest EVENTS_MAX events, oldest first, that the log gives."""
    statuses: dict[str, Status] = {}
    events: deque[Event] = deque(maxlen=EVENTS_MAX)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return statuses, events
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8"))
            if not isinstance(entry, dict):
                raise StatusError("not a JSON object")
            if entry.get("op") != "status":
                continue
            _fold(statuses, events, {"ts": entry["ts"], **_normalize(entry["thread"], entry["text"])})
        except (ValueError, KeyError, TypeError) as error:
            if warn:
                print(f"agentmap: {path}:{number}: skipped log line: {error!r}", file=sys.stderr)
    return statuses, events
