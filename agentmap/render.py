"""HTML of the dashboard. See docs/plans/agentmap.md, section render.py.

The server renders the whole `<main id="app">` element on each change, and
Datastar morphs it into the page. Every element that persists between renders
has a stable `id`, so that the morph changes only what changed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from agentmap.markup import Safe, html
from agentmap.plan import Item, Plan
from agentmap.t3 import AgentThread
from agentmap.treemap import Cell, locate

# The size in CSS px that the server passes to `treemap.build_cells`.
# The page draws the map in a box with the same aspect ratio.
MAP_W = 1080.0
MAP_H = 600.0

# OKLCH hue angles, one per root thread, in order of creation.
HUES = (75, 230, 150, 320, 25, 190, 280, 110)

# An idle child that is quiet for longer than this shows as one line.
COMPACT_AFTER = timedelta(minutes=10)

STATE_CLASS = {"working": "working", "needs_you": "needs-you", "idle": "idle", "failed": "failed"}
GLYPH = {"done": "✓", "doing": "◐", "todo": "○"}


def format_age(delta: timedelta) -> str:
    """The age in the largest whole unit: `now`, `12m`, `3h` or `2d`."""
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render_page(app_html: str) -> Safe:
    """The whole document. It opens the event stream when it loads.

    `app_html` is the HTML of the app element, so it is written unchanged.
    """
    return html(
        t"<!doctype html>\n"
        t'<html lang="en"><head><meta charset="utf-8">'
        t'<meta name="viewport" content="width=device-width, initial-scale=1">'
        t"<title>agentmap</title>"
        t'<link rel="stylesheet" href="/static/app.css">'
        t'<script type="module" src="/static/datastar.js"></script>'
        t"</head>\n"
        t"<body data-init=\"@get('/stream', {{openWhenHidden: true}})\">\n"
        t"{Safe(app_html)}\n"
        t"</body></html>\n"
    )


def render_app(threads: list[AgentThread], plans: dict[str, Plan], cells: list[Cell],
               now: datetime, project_name: str) -> Safe:
    """One `<main id="app">` element with the header, the map and the tree."""
    forest = _Forest(threads)
    return html(t'<main id="app">{_header(threads, now, project_name)}'
                t'{_map(forest, cells)}{_tree(forest, plans, now)}</main>')


class _Forest:
    """The ownership tree of the snapshot, with a tag and a hue per thread."""

    def __init__(self, threads: list[AgentThread]):
        ids = {t.id for t in threads}
        order = sorted(threads, key=lambda t: (t.created_at, t.id))
        self.children: dict[str | None, list[AgentThread]] = defaultdict(list)
        for t in order:
            parent = t.parent_id if t.parent_id in ids else None
            self.children[parent].append(t)
        self.roots = self.children[None]
        self.tag: dict[str, str] = {}
        self.hue: dict[str, int] = {}
        for i, root in enumerate(self.roots):
            self._assign(root, _letters(i), HUES[i % len(HUES)])

    def _assign(self, t: AgentThread, tag: str, hue: int) -> None:
        self.tag[t.id] = tag
        self.hue[t.id] = hue
        sep = "." if any(ch.isdigit() for ch in tag) else ""
        for i, kid in enumerate(self.children[t.id], start=1):
            self._assign(kid, f"{tag}{sep}{i}", hue)

    def walk(self):
        """All threads, parents before children, in tag order."""
        stack = list(reversed(self.roots))
        while stack:
            t = stack.pop()
            yield t
            stack.extend(reversed(self.children[t.id]))

    def busy(self, t: AgentThread) -> bool:
        """True if the thread or a descendant is working or needs the user."""
        return t.state in ("working", "needs_you") or any(self.busy(k) for k in self.children[t.id])


def _letters(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _id(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"


def _header(threads: list[AgentThread], now: datetime, project_name: str) -> Safe:
    counts = defaultdict(int)
    for t in threads:
        counts[t.state] += 1
    parts = [html(t'<span class="n {STATE_CLASS[s]}">{counts[s]} {label}</span>')
             for s, label in (("working", "working"), ("needs_you", "needs you"),
                              ("idle", "idle"), ("failed", "failed"))
             if counts[s]]
    # Each part is Safe, so the parts joined by spaces are Safe too.
    summary = Safe(" ".join(parts)) if parts else "no threads"
    return html(
        t'<header id="top">'
        t'<h1>{project_name}</h1>'
        t'<div class="counts">{summary}</div>'
        t'<time>{now.astimezone():%H:%M}</time>'
        t"</header>"
    )


def _map(forest: _Forest, cells: list[Cell]) -> Safe:
    pins: dict[str, list[Safe]] = defaultdict(list)
    trails: dict[str, list[Safe]] = defaultdict(list)
    busy_cells: set[str] = set()
    for t in forest.walk():
        hue = forest.hue[t.id]
        seen: set[str] = set()
        for path in t.touched:
            cell = locate(cells, path)
            if cell is None or cell.key in seen:
                continue
            if not seen:
                # The newest located path holds the thread's dot.
                pins[cell.key].append(html(
                    t'<span class="dot {STATE_CLASS[t.state]}" style="--hue:{hue}">'
                    t'{forest.tag[t.id]}</span>'))
                if t.state in ("working", "needs_you"):
                    busy_cells.add(cell.key)
            else:
                trails[cell.key].append(html(t'<i class="trail" style="--hue:{hue}"></i>'))
            seen.add(cell.key)
    empty = None if cells else html(t'<p class="empty">No files to map.</p>')
    divs = [_cell(c, c.key in busy_cells, pins[c.key], trails[c.key]) for c in cells]
    return html(t'<section id="map"><div class="map-box">{empty}{divs}</div></section>')


def _cell(c: Cell, busy: bool, pins: list[Safe], trails: list[Safe]) -> Safe:
    cls = f"cell d{c.depth}" + (" busy" if busy else "")
    return html(
        t'<div class="{cls}" id="{_id("c", c.key)}" data-cell="{c.key}" style="'
        t'left:{100 * c.x / MAP_W:.3f}%;top:{100 * c.y / MAP_H:.3f}%;'
        t'width:{100 * c.w / MAP_W:.3f}%;height:{100 * c.h / MAP_H:.3f}%">'
        t'<span class="label">{_label(c)}</span>'
        t'<div class="pins">{pins}{trails}</div>'
        t"</div><!--/cell-->")


def _label(cell: Cell) -> str:
    """The label of a cell. The cell of files directly in a directory says `files`."""
    return "files" if cell.label == "." else cell.label


def _tree(forest: _Forest, plans: dict[str, Plan], now: datetime) -> Safe:
    roots = sorted(forest.roots, key=lambda r: not forest.busy(r))
    body = [_card(r, forest, plans, now, is_root=True) for r in roots]
    if not body:
        body = html(t'<p class="empty">No threads in the window.</p>')
    return html(t'<section id="tree">{body}</section>')


def _card(t: AgentThread, forest: _Forest, plans: dict[str, Plan], now: datetime,
          is_root: bool) -> Safe:
    tag = forest.tag[t.id]
    age = format_age(now - t.updated_at)
    compact = (not is_root and t.state == "idle" and now - t.updated_at > COMPACT_AFTER)
    state_label = {"working": "working", "needs_you": "needs you",
                   "idle": f"idle {age}", "failed": f"failed {age}"}[t.state]
    cls = f"card {STATE_CLASS[t.state]}" + (" compact" if compact else "")
    head = html(
        t"<header>"
        t'<span class="tag">{tag}</span>'
        t'<h3 class="title">{t.title}</h3>'
        t'<span class="state">{state_label}</span>'
        t"</header>"
    )
    plan = plans.get(t.id)
    body: list[Safe] = []
    if compact:
        if t.last_said:
            body.append(html(t'<div class="said">{t.last_said}</div>'))
    else:
        meta = " · ".join(x for x in (t.provider, t.model) if x)
        started = format_age(now - t.created_at)
        body.append(html(t'<div class="meta">{meta} · started {started} ago</div>'))
        if plan and plan.status:
            body.append(html(t'<div class="status">◆ {plan.status}</div>'))
        if t.now:
            body.append(html(t'<div class="now">▸ {t.now}</div>'))
        if t.thought:
            body.append(html(t'<div class="thought">{t.thought}</div>'))
        if t.last_said:
            body.append(html(t'<div class="said">↳ {t.last_said}</div>'))
        if plan and (plan.goal or plan.items):
            body.append(_plan(t.id, plan))
    kids = [_card(k, forest, plans, now, is_root=False) for k in forest.children[t.id]]
    if kids:
        body.append(html(t'<div class="kids">{kids}</div>'))
    return html(t'<article class="{cls}" id="{_id("t", t.id)}" data-tag="{tag}" '
                t'style="--hue:{forest.hue[t.id]}">{head}{body}</article>')


def _plan(thread_id: str, plan: Plan) -> Safe:
    goal = html(t'<div class="goal">{plan.goal}</div>') if plan.goal else None
    items = None
    if plan.items:
        items = html(t'<ol class="items">{[_item(thread_id, i) for i in plan.items]}</ol>')
    return html(t'<div class="plan">{goal}{items}</div>')


def _item(thread_id: str, item: Item) -> Safe:
    count = None
    if item.children:
        done = sum(k.state == "done" for k in item.children)
        count = html(t' <span class="count">({done}/{len(item.children)})</span>')
    kids = None
    if item.children and item.state != "done":
        kids = html(t'<ol class="items">{[_item(thread_id, k) for k in item.children]}</ol>')
    return html(t'<li class="item {item.state}" id="{_id("i", thread_id + "#" + item.id)}">'
                t'<span class="g">{GLYPH[item.state]}</span><span class="t">{item.text}</span>'
                t'{count}{kids}</li>')
