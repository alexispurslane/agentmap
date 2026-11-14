"""Find the thread that made an MCP call without a thread header.

All Codex threads of T3 Code share one `codex app-server` process, so no
environment variable can name the calling thread. T3 Code records every MCP
call as a turn item with its thread, tool name and arguments, and the item
stays active while the call runs. The finder looks up that record.

Two rules keep the match exact:

- One record serves one request. The finder remembers the records that it
  used (claims them) for `FORGET_AFTER` seconds. The lookup drops records
  older than five minutes, so a record cannot match again after its claim
  is forgotten.
- If records of two different threads match at once, the finder cannot tell
  them apart. It raises an error that asks the agent to call again.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

# How long a claim lasts, in seconds. It must be longer than the lookup's
# five-minute limit on the age of a record.
FORGET_AFTER = 600.0

_NOT_FOUND = """\
agentmap could not find this call in T3 Code's records, so it does not know \
which thread made it. If this is Claude Code, give the agentmap server in \
.mcp.json "headers": {"X-T3-Thread": "${T3CODE_THREAD_ID}"}. Otherwise, make \
sure that T3 Code started this agent."""

_AMBIGUOUS = """\
Two threads made this same call at the same moment, so agentmap cannot tell \
which one you are. Call the tool again."""


class CallerError(Exception):
    """The finder cannot name the calling thread. The message is for the agent."""


class CallerFinder:
    """Callable as `finder(tool, arguments) -> thread_id`. Safe across threads."""

    def __init__(self, lookup: Callable[[str, dict], list[tuple[str, str]]], *,
                 timeout: float = 2.0, interval: float = 0.05,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        """`lookup(tool, arguments)` returns the active matching records as
        (record id, thread id) pairs, oldest first."""
        self._lookup = lookup
        self._timeout = timeout
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._claims: dict[str, float] = {}
        self._lock = threading.Lock()

    def __call__(self, tool: str, arguments: dict) -> str:
        deadline = self._clock() + self._timeout
        while True:
            with self._lock:
                now = self._clock()
                self._claims = {k: t for k, t in self._claims.items()
                                if now - t < FORGET_AFTER}
                free = [(rec, thread) for rec, thread in self._lookup(tool, arguments)
                        if rec not in self._claims]
                if len({thread for _, thread in free}) > 1:
                    raise CallerError(_AMBIGUOUS)
                if free:
                    record, thread = free[0]
                    self._claims[record] = now
                    return thread
            if self._clock() >= deadline:
                raise CallerError(_NOT_FOUND)
            self._sleep(self._interval)
