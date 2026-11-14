from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.epic_column.epic_column import epic_column
from agentmap.components.view import Forest
from agentmap.tickets import Group, Ticket


def epic_strip(groups: list[Group], tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
               forest: Forest, now: datetime) -> str:
    """The bottom panel `<section id="epics">`: one column per active group, side by side.

    The server patches it as its own fragment, apart from `<main id="app">`.
    """
    in_progress = sum(1 for g in groups for t in g.members if t.status == "in_progress")
    n = len(groups)
    return render("epic_strip", {
        "columns": [epic_column(g, tickets, links, forest, now) for g in groups],
        "summary": f"{n} epic{'' if n == 1 else 's'} · {in_progress} in progress",
    })
