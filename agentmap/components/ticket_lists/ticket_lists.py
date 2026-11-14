from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.ticket_row.ticket_row import ticket_row
from agentmap.components.view import Forest, element_id, group_key
from agentmap.tickets import Group, Ticket


def ticket_lists(groups: list[Group], tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
                 forest: Forest, now: datetime) -> str:
    """`<div id="tickets">`: one ticket list per active group, in the event log's place.

    Each list shows while `$epic` holds its group's key. The last section shows
    while `$epic` holds a key that is no longer an active group.
    """
    keys = [element_id("g", group_key(g)) for g in groups]
    lists = []
    for g, key in zip(groups, keys):
        n = len(g.members)
        lists.append({
            "key": key,
            "id": g.root.id if g.root else "",
            "title": g.root.title if g.root else "No epic",
            "count": f"{n} ticket{'' if n == 1 else 's'}",
            "rows": [ticket_row(t, tickets, links, forest, now) for t in g.members],
        })
    return render("ticket_lists", {
        "lists": lists,
        "keys": "[" + ",".join(f"'{k}'" for k in keys) + "]",
    })
