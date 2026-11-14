"""Events: dated facts about agent threads, for the page's event log and fading marks.

Two sources make events. `t3.T3Reader.events` reads what the threads did in
T3 Code. `plan.PlanStore.events` reads what the agents posted through MCP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# What happened. The first five come from T3 Code, the last five from the plan store.
KINDS = ("started", "edits", "asks", "finished", "failed", "goal", "status", "added", "doing",
         "done")


@dataclass(frozen=True)
class Event:
    at: datetime      # When it happened, timezone-aware UTC.
    thread_id: str    # The T3 Code thread it happened in.
    kind: str         # One of KINDS.
    text: str         # The detail, for example a path or an item text. "" when there is none.
