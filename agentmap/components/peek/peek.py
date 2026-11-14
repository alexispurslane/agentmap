from urllib.parse import quote

from agentmap.components._template import render
from agentmap.components.view import element_id


def peek(ticket_id: str) -> str:
    """Datastar attributes that open the hover box of one ticket.

    On mouseenter, the element sets `$peek` to the ticket's element id and
    loads the box's content from `/peek/<id>`. On mouseleave, it clears
    `$peek`. The CSS makes the hovered element the anchor of the box. Other
    templates include it as `{{> peek}}` with `peek_data`.
    """
    return render("peek", peek_data(ticket_id))


def peek_data(ticket_id: str) -> dict:
    """The template data of `{{> peek}}`."""
    return {"dom": element_id("t", ticket_id), "url": quote(ticket_id, safe="")}
