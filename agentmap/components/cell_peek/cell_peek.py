from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_chip.agent_chip import agent_chip
from agentmap.components.view import Forest, cell_dir, cell_edits, cell_title, element_id, format_age
from agentmap.events import Event
from agentmap.treemap import Cell

# How many files a hover box and a pinned box show.
HOVER_FILES = 8
PINNED_FILES = 40


def cell_peek(key: str, cells: list[Cell], forest: Forest, events: list[Event], now: datetime,
              pinned: bool = False) -> str:
    """The content of the box for the map cell `key`: its newest edited files.

    Each line is a file's path inside the cell's folder, the age of its newest
    edit, and the tags of the threads that edited it, newest editor first. A
    hover box shows HOVER_FILES files. A pinned box shows PINNED_FILES and a
    button that unpins it. The content shows only while the box is for this
    cell, as for a ticket's box.
    """
    folder = cell_dir(key)
    files = cell_edits(cells, forest, events).get(key, [])
    return render("cell_peek", {
        "dom": element_id("c", key), "title": cell_title(key), "pinned": pinned,
        "any": bool(files),
        "files": [{"path": f.path[len(folder):], "age": format_age(now - f.at),
                   "chips": [agent_chip(forest.tag[t], forest.hue[t]) for t in f.threads]}
                  for f in files[:PINNED_FILES if pinned else HOVER_FILES]],
    })
