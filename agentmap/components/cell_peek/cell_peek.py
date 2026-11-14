from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_chip.agent_chip import agent_chip
from agentmap.components.view import (Forest, cell_dir, cell_edits, cell_title, element_id, format_age,
                                      format_size, heaviest_files)
from agentmap.events import Event
from agentmap.treemap import Cell

# How many files a hover box and a pinned box show.
HOVER_FILES = 8
PINNED_FILES = 40
# How many of its heaviest files a box shows, below the edited files.
HEAVIEST_FILES = 5


def cell_peek(key: str, cells: list[Cell], forest: Forest, events: list[Event], now: datetime,
              pinned: bool = False, weights: Mapping[str, int] | None = None) -> str:
    """The content of the box for the map cell `key`: its newest edited files.

    Each line is a file's path inside the cell's folder, the age of its newest
    edit, and the tags of the threads that edited it, newest editor first. A
    hover box shows HOVER_FILES files. A pinned box shows PINNED_FILES and a
    button that unpins it. The content shows only while the box is for this
    cell, as for a ticket's box.

    Below them, under a heading, every box lists the cell's HEAVIEST_FILES
    files with the most weight (`weights`, the map's weight of each file),
    each with its weight in place of an age. A cell without edits in the event
    window says so above that list. A file can show in both lists.
    """
    folder = cell_dir(key)
    edits = cell_edits(cells, forest, events).get(key, [])
    files = [{"path": f.path[len(folder):], "age": format_age(now - f.at),
              "chips": [agent_chip(forest.tag[t], forest.hue[t]) for t in f.threads]}
             for f in edits[:PINNED_FILES if pinned else HOVER_FILES]]
    heavy = [{"path": path[len(folder):], "age": format_size(w), "chips": []}
             for path, w in heaviest_files(cells, key, weights or {}, HEAVIEST_FILES)]
    return render("cell_peek", {
        "dom": element_id("c", key), "title": cell_title(key), "pinned": pinned,
        "files": files, "heavy": heavy,
    })
