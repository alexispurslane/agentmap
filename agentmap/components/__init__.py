"""The dashboard's HTML, one component per folder.

`components/<name>/<name>.py` holds one pure function with typed arguments,
and `components/<name>/<name>.html` holds the Mustache template it renders.
The server needs `page`, the three fragments `app`, `epic_strip` and
`ticket_lists`, and `ticket_peek` for the hover box or the pinned box of one
ticket.
"""

from agentmap.components.app.app import app
from agentmap.components.epic_strip.epic_strip import epic_strip
from agentmap.components.page.page import page
from agentmap.components.ticket_lists.ticket_lists import ticket_lists
from agentmap.components.cell_peek.cell_peek import cell_peek
from agentmap.components.ticket_peek.ticket_peek import ticket_peek

__all__ = ["app", "cell_peek", "epic_strip", "page", "ticket_lists", "ticket_peek"]
