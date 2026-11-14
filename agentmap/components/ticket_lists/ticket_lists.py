from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.ticket_row.ticket_row import ticket_row
from agentmap.components.view import Forest, element_id, group_key, tiered
from agentmap.tickets import Group, Ticket


# The heading of each tier of a ticket list.
TIER_LABELS = {"agent": "An agent works on it", "recent": "In progress, changed in 24 hours",
               "ready": "Ready", "blocked": "Blocked"}


def ticket_lists(groups: list[Group], tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
                 forest: Forest, now: datetime) -> str:
    """`<div id="tickets">`: one ticket list per active group, in the event log's place.

    Each list shows while `$epic` holds its group's key. The last section shows
    while `$epic` holds a key that is no longer an active group.

    A list holds its tickets in tiers (see `ticket_tier`), in a column that runs
    from the bottom up, so the most relevant ticket sits next to the epic strip.
    Inside a tier, the most relevant ticket also comes last. Stale claims fold
    into one line at the top, which a click on it opens through `$stale`.
    """
    keys = [element_id("g", group_key(g)) for g in groups]
    lists = []
    for g, key in zip(groups, keys):
        n = len(g.members)
        root_id = g.root.id if g.root else None
        by_tier = tiered(g.members, tickets, links, now)

        def rows(tier: str) -> list[str]:
            return [ticket_row(t, tickets, links, forest, now, root_id) for t in reversed(by_tier[tier])]

        stale = len(by_tier["stale"])
        lists.append({
            "key": key,
            "id": g.root.id if g.root else "",
            "title": g.root.title if g.root else "No epic",
            "count": f"{n} ticket{'' if n == 1 else 's'}",
            "tiers": [{"name": tier, "label": TIER_LABELS[tier], "rows": rows(tier)}
                      for tier in ("agent", "recent", "ready", "blocked") if by_tier[tier]],
            "stale": stale and {
                "label": f"{stale} stale claim{'' if stale == 1 else 's'}: in progress, no agent, no change in 24 hours",
                "rows": rows("stale")},
        })
    return render("ticket_lists", {
        "lists": lists,
        "keys": "[" + ",".join(f"'{k}'" for k in keys) + "]",
    })
