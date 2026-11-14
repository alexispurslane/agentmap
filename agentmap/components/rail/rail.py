from collections import Counter
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.view import STATE_CLASS, clock
from agentmap.t3 import AgentThread

_LABELS = (("working", "working"), ("needs_you", "needs you"), ("idle", "idle"), ("failed", "failed"))


def rail(project_name: str, threads: list[AgentThread], now: datetime) -> str:
    """The top rail: project name, the number of threads in each state, the clock.

    Every state keeps its place, so a count of zero shows in a quiet style.
    """
    counts = Counter(t.state for t in threads)
    return render("rail", {
        "project": project_name,
        "counts": [{"n": counts[s], "label": label,
                    "cls": STATE_CLASS[s] if counts[s] else "none"} for s, label in _LABELS],
        "clock": clock(now),
    })
