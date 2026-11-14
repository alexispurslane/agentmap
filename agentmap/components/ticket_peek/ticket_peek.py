from agentmap.components._template import render
from agentmap.components.view import element_id
from agentmap.markdown import render as markdown
from agentmap.tickets import Ticket


def ticket_peek(ticket: Ticket, pinned: bool = False) -> str:
    """The content of the box for one ticket: its title and rendered description.

    A hover box leaves the notes out. A pinned box adds them, newest first, and
    a button that unpins it. The content shows only while the box is for this
    ticket: the pinned ticket `$pin`, or else the hovered ticket `$peek`. So a
    late answer for a ticket that the pointer already left stays hidden.
    """
    return render("ticket_peek", {
        "dom": element_id("t", ticket.id), "title": ticket.title, "pinned": pinned,
        "text": markdown(ticket.body) if ticket.body.strip() else "",
        "notes": [{"at": n.at.astimezone().strftime("%Y-%m-%d %H:%M"), "text": markdown(n.text)}
                  for n in reversed(ticket.notes)] if pinned else [],
    })
