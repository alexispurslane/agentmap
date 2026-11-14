from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_chip.agent_chip import agent_chip
from agentmap.components.link.link import link_data
from agentmap.components.view import Forest, age_class, element_id, format_age, ticket_state
from agentmap.tickets import Ticket, blocked_by


def ticket_row(ticket: Ticket, tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
               forest: Forest, now: datetime) -> str:
    """One ticket in a ticket list: a line of metadata, then the title.

    The row's head is a ticket mark: a hover focuses the ticket's threads and
    opens its hover box, and a click pins the box.
    """
    workers = links.get(ticket.id, [])
    newest_note = ticket.notes[-1].at if ticket.notes else None
    n = len(ticket.notes)
    return render("ticket_row", {
        **link_data([forest.tag[t] for t in workers], forest.hue[workers[0]] if workers else None, ticket.id, "r"),
        "dom": element_id("t", ticket.id),
        "state": ticket_state(ticket, tickets), "age": age_class(now, ticket.changed_at),
        "id": ticket.id, "priority": ticket.priority, "type": ticket.type, "title": ticket.title,
        "blocked": ", ".join(blocked_by(ticket, tickets)),
        "agents": [agent_chip(forest.tag[t], forest.hue[t]) for t in workers],
        "tags": ", ".join(ticket.tags),
        "notes": f"{n} note{'' if n == 1 else 's'} · {format_age(now - newest_note)} ago" if n else "",
        "notes_age": age_class(now, newest_note),
    })
