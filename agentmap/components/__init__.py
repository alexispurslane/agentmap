"""The dashboard's HTML, one component per folder.

`components/<name>/<name>.py` holds one pure function with typed arguments,
and `components/<name>/<name>.html` holds the Mustache template it renders.
The server needs only `page` and `app`.
"""

from agentmap.components.app.app import app
from agentmap.components.page.page import page

__all__ = ["app", "page"]
