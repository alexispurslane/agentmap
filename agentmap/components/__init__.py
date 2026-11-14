"""The dashboard's HTML, one component per folder.

`components/<name>/<name>.py` holds one pure function with typed arguments,
and `components/<name>/<name>.html` holds the Mustache template it renders.
The server needs `page`, the three fragments `app`, `epic_strip` and
`ticket_lists`, and `ticket_body` for the text of one ticket.
"""

from agentmap.components.app.app import app
from agentmap.components.epic_strip.epic_strip import epic_strip
from agentmap.components.page.page import page
from agentmap.components.ticket_body.ticket_body import ticket_body
from agentmap.components.ticket_lists.ticket_lists import ticket_lists

__all__ = ["app", "epic_strip", "page", "ticket_body", "ticket_lists"]
