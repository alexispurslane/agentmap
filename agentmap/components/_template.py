"""Render a component's Mustache template with chevron.

Each component lives in `components/<name>/` as `<name>.py`, which holds one
pure function, and `<name>.html`, its template. Every template can include any
other component's template as a partial, for example `{{> tag}}`.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Mapping

from agentmap.vendor import chevron

_DIR = Path(__file__).parent


@cache
def templates() -> dict[str, str]:
    """Every component's template by component name, without a final line break."""
    found = {}
    for folder in sorted(_DIR.iterdir()):
        file = folder / f"{folder.name}.html"
        if file.is_file():
            found[folder.name] = file.read_text(encoding="utf-8").rstrip("\n")
    return found


def render(name: str, data: Mapping[str, Any]) -> str:
    """The HTML of the template `name` for `data`. Values in `{{ }}` are escaped."""
    return chevron.render(templates()[name], data, partials_path=None,
                          partials_dict=templates())
