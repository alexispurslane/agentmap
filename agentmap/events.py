"""Events: dated facts about agent threads, for the page's event log and fading marks.

Two sources make events. `t3.T3Reader.events` reads what the threads did in
T3 Code. `status.StatusStore.events` reads the status lines that agents posted through MCP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# What happened. The first five come from T3 Code, the last from the status store.
KINDS = ("started", "edits", "asks", "finished", "failed", "status")


@dataclass(frozen=True)
class Event:
    at: datetime      # When it happened, timezone-aware UTC.
    thread_id: str    # The T3 Code thread it happened in.
    kind: str         # One of KINDS.
    text: str         # The detail, for example a path or a status line. "" when there is none.
