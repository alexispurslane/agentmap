from agentmap.components._template import render
from agentmap.components.link.link import link_data


def ticket_badge(ticket_id: str, tags: list[str], hue: float | None, place: str) -> str:
    """A ticket's id as a ticket mark on an agent row.

    `tags` are the threads that work on the ticket and `hue` the hue of the
    first. A hover focuses them and opens the hover box, and a click pins it.
    `place` makes the mark's id unique on the page, for example "b-A2".
    """
    return render("ticket_badge", {"id": ticket_id, **link_data(tags, hue, ("t", ticket_id), place)})
