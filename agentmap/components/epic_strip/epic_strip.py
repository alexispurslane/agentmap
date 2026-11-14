from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.epic_column.epic_column import epic_column
from agentmap.components.view import Forest, group_threads, tiered
from agentmap.tickets import Group, Ticket


def epic_strip(groups: list[Group], tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
               forest: Forest, now: datetime) -> str:
    """The bottom panel `<section id="epics">`: one column per active group, side by side.

    Groups with more agents come first.

    The server patches it as its own fragment, apart from `<main id="app">`.
    """
    tiers = [tiered(g.members, tickets, links, now) for g in groups]
    live = sum(len(t["agent"]) + len(t["recent"]) for t in tiers)
    stale = sum(len(t["stale"]) for t in tiers)
    # Groups with more agents come first; the sort keeps the given order otherwise.
    ordered = sorted(groups, key=lambda g: -len(group_threads(g, links, forest)))
    n = len(groups)
    return render("epic_strip", {
        "columns": [epic_column(g, tickets, links, forest, now) for g in ordered],
        "summary": f"{n} epic{'' if n == 1 else 's'} · {live} live · {stale} stale",
    })
