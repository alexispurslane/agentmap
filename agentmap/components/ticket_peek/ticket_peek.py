from agentmap.components._template import render
from agentmap.components.view import element_id
from agentmap.markdown import render as markdown
from agentmap.tickets import Ticket


def ticket_peek(ticket: Ticket) -> str:
    """The content of the hover box for one ticket: its title and rendered description.

    The notes are left out. The content shows only while `$peek` holds this
    ticket, so a late answer for a ticket that the pointer already left stays
    hidden.
    """
    return render("ticket_peek", {
        "dom": element_id("t", ticket.id), "title": ticket.title,
        "text": markdown(ticket.body) if ticket.body.strip() else "",
    })
