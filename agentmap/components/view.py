"""Facts that several components share: tags, hues, ages and the summary line.

Everything here is pure: the same snapshot and the same `now` give the same
result.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from agentmap.status import Status
from agentmap.t3 import AgentThread

# Each thread gets its own OKLCH hue. In order of creation, each hue is the
# one before plus the golden angle, so threads close in that order get hues
# far apart: at least 32 degrees for 8 threads, 20 for 12.
FIRST_HUE = 75.0
GOLDEN_ANGLE = 137.50776405

# The ink ramp: a mark younger than each limit, in seconds, gets that class.
# Anything older gets "a4".
AGE_STEPS = ((60, "a0"), (5 * 60, "a1"), (15 * 60, "a2"), (60 * 60, "a3"))

STATE_CLASS = {"working": "working", "needs_you": "needs-you", "idle": "idle", "failed": "failed"}

# Statuses of a delegation record that mean the subagent's task ended without
# failing. A "failed" delegation is left out on purpose: a failed subagent stays
# on the board, so that the user sees the failure without reading the log.
SETTLED_DELEGATIONS = frozenset({"completed", "cancelled", "interrupted"})


def age_class(now: datetime, at: datetime | None) -> str:
    """The ink step of a mark made at `at`: "a0" (under 1 min) to "a4" (1 h or more)."""
    if at is None:
        return "a4"
    seconds = (now - at).total_seconds()
    for limit, name in AGE_STEPS:
        if seconds < limit:
            return name
    return "a4"


def format_age(delta: timedelta) -> str:
    """The age in the largest whole unit: `now`, `12m`, `3h` or `2d`."""
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def clock(at: datetime) -> str:
    """The local wall-clock time of `at`, as `14:37`."""
    return at.astimezone().strftime("%H:%M")


def element_id(prefix: str, key: str) -> str:
    """A stable, unique element id, so that Datastar's morph keeps the element."""
    return f"{prefix}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def settled(t: AgentThread) -> bool:
    """True if the thread is a subagent whose task ended and that does nothing now.

    A root thread has no delegation, so it never settles. A settled thread that
    gets a new task, or a new message, is live again.
    """
    return t.delegation in SETTLED_DELEGATIONS and t.state not in ("working", "needs_you")


class Forest:
    """The ownership tree of a snapshot, with a tag and a hue for each thread.

    Tags and hues come from every thread in the snapshot, so that they stay
    stable while subagents settle and resume. `roots`, `children` and `walk()`
    hold only the shown threads: the threads that are not settled, and the
    settled threads that have a shown descendant.
    """

    def __init__(self, threads: list[AgentThread]):
        ids = {t.id for t in threads}
        order = sorted(threads, key=lambda t: (t.created_at, t.id))
        self.by_id = {t.id: t for t in threads}
        everyone: dict[str | None, list[AgentThread]] = defaultdict(list)
        for t in order:
            parent = t.parent_id if t.parent_id in ids else None
            everyone[parent].append(t)
        self.tag: dict[str, str] = {}
        self.hue = {t.id: round((FIRST_HUE + i * GOLDEN_ANGLE) % 360, 1)
                    for i, t in enumerate(order)}
        for i, root in enumerate(everyone[None]):
            self._assign(everyone, root, _letters(i))

        self.children: dict[str | None, list[AgentThread]] = defaultdict(list)

        def keep(t: AgentThread) -> bool:
            kids = [k for k in everyone[t.id] if keep(k)]
            self.children[t.id] = kids
            return not settled(t) or bool(kids)

        self.roots = [r for r in everyone[None] if keep(r)]

    def _assign(self, everyone: dict[str | None, list[AgentThread]], t: AgentThread,
                tag: str) -> None:
        self.tag[t.id] = tag
        sep = "." if any(ch.isdigit() for ch in tag) else ""
        for i, kid in enumerate(everyone[t.id], start=1):
            self._assign(everyone, kid, f"{tag}{sep}{i}")

    def walk(self):
        """All shown threads, parents before children, in tag order."""
        stack = list(reversed(self.roots))
        while stack:
            t = stack.pop()
            yield t
            stack.extend(reversed(self.children[t.id]))

    def busy(self, t: AgentThread) -> bool:
        """True if the thread or a descendant is working or needs the user."""
        return t.state in ("working", "needs_you") or any(self.busy(k) for k in self.children[t.id])


def _letters(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


@dataclass(frozen=True)
class Summary:
    """The one line that says what a thread is about now.

    kind: "status" (posted by the agent through MCP), "said" (the first
    sentence of its last message) or "thought" (of its newest thinking).
    """

    kind: str
    text: str
    at: datetime | None


def summary(thread: AgentThread, status: Status | None) -> Summary | None:
    """The newest of the posted status, the last message and the newest thought.

    A source without a time counts as older than any source with one. On a
    tie, the posted status wins, then the message, then the thought.
    """
    sources = []
    if status is not None:
        sources.append(Summary("status", status.text, status.at))
    if thread.last_said:
        sources.append(Summary("said", thread.last_said, thread.said_at))
    if thread.thought:
        sources.append(Summary("thought", thread.thought, thread.thought_at))
    if not sources:
        return None
    oldest = datetime.min.replace(tzinfo=thread.created_at.tzinfo)
    # max() keeps the first of equal keys, so the order above breaks ties.
    return max(sources, key=lambda s: s.at or oldest)
