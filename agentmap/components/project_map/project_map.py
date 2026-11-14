import math
from collections import defaultdict
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.fade.fade import fade_data
from agentmap.components.view import STATE_CLASS, Forest, element_id
from agentmap.treemap import Cell, locate

# The map's own coordinates, in CSS px. The server builds the cells at this
# size, and the page draws the map in a box with the same aspect ratio.
MAP_W = 1080.0
MAP_H = 600.0

# Threads that pass through one cell sit on a small grid of slots around the
# cell's center, so their routes and dots do not cover each other.
SLOT_DX = 46.0
SLOT_DY = 30.0
SLOTS_PER_ROW = 4
# How far a slot keeps from the cell's edges: sides, top (under the label), bottom.
INSET_X, INSET_TOP, INSET_BOTTOM = 14.0, 22.0, 10.0


def project_map(cells: list[Cell], forest: Forest) -> str:
    """The map module: the treemap, each thread's route, and each thread's dot.

    A thread's route runs through the cells of its touched files, oldest to
    newest, and ends at its dot. The newest segment, the one that ends at the
    dot, starts in the full color of the thread. Every segment and every stop
    fades in real time from the time of its touch, so the route fades toward
    its start, and the route of an agent that stops moving fades away. A hover on the thread draws its whole route at full color.
    """
    # The cells each thread touched, newest first, each cell once with its newest time.
    visits: dict[str, list[tuple[Cell, datetime]]] = {}
    for t in forest.walk():
        seen: dict[str, tuple[Cell, datetime]] = {}
        for touch in t.touched:
            cell = locate(cells, touch.path)
            if cell is not None and cell.key not in seen:
                seen[cell.key] = (cell, touch.at)
        visits[t.id] = list(seen.values())

    # Slots: in each cell, the threads that visit it, in tag order.
    in_cell: dict[str, list[str]] = defaultdict(list)
    for t in forest.walk():
        for cell, _ in visits[t.id]:
            in_cell[cell.key].append(t.id)

    def point(cell: Cell, thread_id: str) -> tuple[float, float]:
        members = in_cell[cell.key]
        k, n = members.index(thread_id), len(members)
        cols = min(n, SLOTS_PER_ROW)
        rows = math.ceil(n / SLOTS_PER_ROW)
        col, row = k % SLOTS_PER_ROW, k // SLOTS_PER_ROW
        x = cell.x + cell.w / 2 + (col - (cols - 1) / 2) * SLOT_DX
        y = cell.y + cell.h / 2 + (row - (rows - 1) / 2) * SLOT_DY
        x = min(max(x, cell.x + INSET_X), cell.x + cell.w - INSET_X)
        y = min(max(y, cell.y + INSET_TOP), cell.y + cell.h - INSET_BOTTOM)
        return round(x, 1), round(y, 1)

    routes, dots, busy = [], [], set()
    for t in forest.walk():
        stops = [(point(cell, t.id), at) for cell, at in reversed(visits[t.id])]
        if not stops:
            continue
        hue, tag = forest.hue[t.id], forest.tag[t.id]
        segments = [{"d": f"M{x1} {y1}H{x2}V{y2}", **fade_data(at)}
                    for ((x1, y1), _), ((x2, y2), at) in zip(stops, stops[1:])]
        if segments:
            segments[-1]["now"] = True
            routes.append({"hue": hue, "tag": tag, "segments": segments,
                           "stops": [{"x": x, "y": y, **fade_data(at)} for (x, y), at in stops[:-1]]})
        (x, y), _ = stops[-1]
        dots.append({"tag": tag, "hue": hue, "enter": "mouseenter",
                     "state": STATE_CLASS[t.state],
                     "x": round(100 * x / MAP_W, 3), "y": round(100 * y / MAP_H, 3)})
        if t.state in ("working", "needs_you"):
            busy.add(visits[t.id][0][0].key)

    return render("project_map", {
        "cells": [{"id": element_id("c", c.key), "key": c.key, "depth": c.depth,
                   "label": "files" if c.label == "." else c.label, "busy": c.key in busy,
                   "x": round(100 * c.x / MAP_W, 3), "y": round(100 * c.y / MAP_H, 3),
                   "w": round(100 * c.w / MAP_W, 3), "h": round(100 * c.h / MAP_H, 3)}
                  for c in cells],
        "routes": routes,
        "dots": dots,
    })
