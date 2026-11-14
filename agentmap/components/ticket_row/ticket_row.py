from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_chip.agent_chip import agent_chip
from agentmap.components.link.link import link_data
from agentmap.components.view import Forest, age_class, element_id, format_age, ticket_path, ticket_tier
from agentmap.tickets import Ticket, blocked_by


def ticket_row(ticket: Ticket, tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
               forest: Forest, now: datetime, root_id: str | None = None) -> str:
    """One ticket in a ticket list: a line of metadata, then the title.

    The row's class is its tier. A live row (tier "agent" or "recent") also
    shows its path: the ids of its ancestors below the group root `root_id`.

    The row's head is a ticket mark: a hover focuses the ticket's threads and
    opens its hover box, and a click pins the box.
    """
    workers = links.get(ticket.id, [])
    tier = ticket_tier(ticket, tickets, links, now)
    newest_note = ticket.notes[-1].at if ticket.notes else None
    n = len(ticket.notes)
    return render("ticket_row", {
        **link_data([forest.tag[t] for t in workers], forest.hue[workers[0]] if workers else None, ("t", ticket.id), "r"),
        "dom": element_id("t", ticket.id),
        "state": tier, "age": age_class(now, ticket.changed_at),
        "path": " › ".join(ticket_path(ticket, tickets, root_id)) if tier in ("agent", "recent") else "",
        "id": ticket.id, "priority": ticket.priority, "type": ticket.type, "title": ticket.title,
        "blocked": ", ".join(blocked_by(ticket, tickets)),
        "agents": [agent_chip(forest.tag[t], forest.hue[t]) for t in workers],
        "tags": ", ".join(ticket.tags),
        "notes": f"{n} note{'' if n == 1 else 's'} · {format_age(now - newest_note)} ago" if n else "",
        "notes_age": age_class(now, newest_note),
    })
