from agentmap.components._template import render
from agentmap.markdown import render as markdown
from agentmap.tickets import Ticket


def ticket_body(ticket: Ticket) -> str:
    """A ticket's rendered text, then its notes newest first, for the row's text box."""
    return render("ticket_body", {
        "text": markdown(ticket.body) if ticket.body.strip() else "",
        "notes": [{"at": n.at.astimezone().strftime("%Y-%m-%d %H:%M"), "text": markdown(n.text)}
                  for n in reversed(ticket.notes)],
    })
