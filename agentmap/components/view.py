"""Facts that several components share: tags, hues, ages and the summary line.

Everything here is pure: the same snapshot and the same `now` give the same
result.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from agentmap.status import Status
from agentmap.t3 import AgentThread
from agentmap.tickets import Group, Ticket, blocked_by, groups

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


# A word that can be a ticket id: letters, digits, `_` and `-`, starting with a letter or digit.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def ticket_agents(forest: Forest, statuses: Mapping[str, Status],
                  ids: Collection[str]) -> dict[str, list[str]]:
    """For each ticket id, the shown threads that work on it, in tag order.

    A thread works on a ticket if its title or its status line names the
    ticket's id as a whole word.
    """
    links: dict[str, list[str]] = {}
    for t in forest.walk():
        for word in named_ids(t.title + " " + (statuses[t.id].text if t.id in statuses else ""), ids):
            links.setdefault(word, []).append(t.id)
    return links


def group_key(group: Group) -> str:
    """The id of the group's root ticket, or "" for the group of tickets without an epic."""
    return group.root.id if group.root else ""


def group_threads(group: Group, links: Mapping[str, list[str]], forest: Forest) -> list[str]:
    """The threads that work on the group's root or on any member, in tag order."""
    ids = {t for ticket in ([group.root] if group.root else []) + list(group.members)
           for t in links.get(ticket.id, [])}
    return [t.id for t in forest.walk() if t.id in ids]


def named_ids(text: str, ids: Collection[str]) -> list[str]:
    """The ids in `ids` that the text names as whole words, in order of first mention."""
    return [w for w in dict.fromkeys(w.rstrip("-_") for w in _WORD.findall(text)) if w in ids]


# Punctuation that a removed id leaves at the start or the end of a title.
_TITLE_EDGES = re.compile(r"^[\s:;,.\-–—]+|[\s:;,\-–—]+$")


def title_without(title: str, ids: Collection[str]) -> str:
    """The title without the ticket ids that it names, and without the punctuation they leave.

    For example "ter-icn3: Unity research" becomes "Unity research", and
    "Mottle (t7-3e22)" becomes "Mottle". A title that is only ids stays as it is.
    """
    text = title
    for found in named_ids(title, ids):
        text = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(found)}(?![A-Za-z0-9_])", "", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = _TITLE_EDGES.sub("", re.sub(r"\s{2,}", " ", text))
    return text or title


# A ticket in progress that changed within this time is live without an agent.
RECENT = timedelta(hours=24)

# The tiers of a ticket list, most relevant first. See `ticket_tier`.
TIERS = ("agent", "recent", "ready", "blocked", "stale")


def ticket_tier(ticket: Ticket, tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
                now: datetime) -> str:
    """The tier of a ticket in its group's list.

    "agent": a shown thread names it. "recent": in progress, changed within
    RECENT. "stale": in progress but neither, a claim that nobody works on.
    Otherwise "blocked" (a dependency is not closed) or "ready". The tk status
    is the claim that an agent stored; the agent link is the work.
    """
    if ticket.id in links:
        return "agent"
    if ticket.status == "in_progress":
        return "recent" if now - ticket.changed_at <= RECENT else "stale"
    return "blocked" if blocked_by(ticket, tickets) else "ready"


def tiered(members, tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
           now: datetime) -> dict[str, list[Ticket]]:
    """The members by tier, each tier in the members' order."""
    found: dict[str, list[Ticket]] = {name: [] for name in TIERS}
    for t in members:
        found[ticket_tier(t, tickets, links, now)].append(t)
    return found


def active_groups(tickets: Mapping[str, Ticket], links: Mapping[str, list[str]]) -> list[Group]:
    """The groups on the board: those with a ticket that a thread names or that is in progress."""
    return groups(tickets, active=lambda t: t.id in links or t.status == "in_progress")


def ticket_path(ticket: Ticket, tickets: Mapping[str, Ticket], root_id: str | None) -> list[str]:
    """The ids of the ticket's ancestors below the group's root, from the top down."""
    path, seen = [], {ticket.id}
    while ticket.parent in tickets and ticket.parent != root_id and ticket.parent not in seen:
        ticket = tickets[ticket.parent]
        seen.add(ticket.id)
        path.append(ticket.id)
    return path[::-1]
