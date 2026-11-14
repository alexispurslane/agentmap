"""The project map: a two-level ordered strip treemap of the project's code.

Each cell is a rectangle whose area is in proportion to the number of lines of
code under it.

- Level 1 has one cell per top-level directory, plus the cell `.` for the files
  directly in the root.
- Level 2 has, inside each top-level directory's cell, one cell per
  subdirectory (key `<dir>/<sub>`), plus the cell `<dir>/.` for the files
  directly in that directory.
- A cell whose weight is under `MIN_SHARE` of the whole project's weight joins
  one `…` cell (key `…` at level 1, `<dir>/…` at level 2). The `…` cell comes
  last in its level. A `…` cell of zero lines is left out.

The layout is the ordered strip treemap of Bederson, Shneiderman and Wattenberg
(2002), "Ordered and quantum treemaps". Items keep their name order. They fill
horizontal strips from top to bottom, left to right within a strip. A new strip
starts when the next item would make the current strip's mean aspect ratio
worse. Because the order is fixed, a small change of weights moves cells only a
little.
"""

import dataclasses
import os
import pathlib
import subprocess

# A cell under this share of the whole project's lines joins its level's `…` cell.
MIN_SHARE = 0.015
# Default height, in CSS px, of the band at the top of each level-1 cell that
# holds its label. Level-2 cells fill the rest of the level-1 cell.
LABEL_INSET = 16.0
# Directories that the walk outside git skips, besides hidden ones.
SKIPPED_DIRS = frozenset({"node_modules", "target", "build"})
# A file with a NUL byte in this many first bytes is binary and counts 0 lines.
BINARY_PROBE = 8192
ELLIPSIS = "…"
ROOT_FILES = "."


@dataclasses.dataclass(frozen=True)
class Cell:
    """One rectangle of the map, in the same units as the map's width and height.

    key: `src`, `.`, `…` at level 1; `src/render`, `src/.`, `src/…` at level 2.
    label: the last part of the key.
    depth: 1 or 2.
    weight: the number of lines of code under the cell.
    """

    key: str
    label: str
    depth: int
    x: float
    y: float
    w: float
    h: float
    weight: int


def code_weights(root: pathlib.Path) -> dict[str, int]:
    """Map each file path, relative to `root` with `/` separators, to its line count.

    In a git repository, the files are the tracked files plus the new files
    that git does not ignore. Elsewhere,
    a walk lists them and skips hidden directories and `SKIPPED_DIRS`. A listed
    path that is not a readable file is left out.
    """
    root = pathlib.Path(root)
    paths = _git_files(root)
    if paths is None:
        paths = _walk_files(root)
    weights = {}
    for rel in paths:
        try:
            weights[rel] = _count_lines(root / rel)
        except OSError:
            continue
    return weights


def _git_files(root):
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in os.fsdecode(out).split("\0") if p]


def _walk_files(root):
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in SKIPPED_DIRS
        )
        rel_dir = pathlib.Path(dirpath).relative_to(root).as_posix()
        for name in sorted(filenames):
            paths.append(name if rel_dir == "." else f"{rel_dir}/{name}")
    return paths


def _count_lines(path):
    with open(path, "rb") as f:
        head = f.read(BINARY_PROBE)
        if b"\0" in head:
            return 0
        lines = head.count(b"\n")
        last = head[-1:]
        for chunk in iter(lambda: f.read(1 << 20), b""):
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    return lines + (last not in (b"", b"\n"))


def build_cells(
    weights: dict[str, int],
    width: float,
    height: float,
    label_inset: float = LABEL_INSET,
) -> list[Cell]:
    """Lay out the map in a `width` x `height` rectangle at the origin.

    Returns the level-1 cells in layout order, each followed by its level-2
    cells. `label_inset` is the height of the label band at the top of each
    level-1 directory cell; it is at most half that cell's height.
    """
    total = sum(weights.values())
    if total <= 0:
        return []
    groups = {}
    for path, lines in weights.items():
        parts = path.split("/")
        top = parts[0] if len(parts) > 1 else ROOT_FILES
        sub = parts[1] if len(parts) > 2 else ROOT_FILES
        subs = groups.setdefault(top, {})
        subs[sub] = subs.get(sub, 0) + lines

    level1 = _join_small({top: sum(subs.values()) for top, subs in groups.items()}, total)
    cells = []
    for (name, weight), (x, y, w, h) in zip(level1, _strips(level1, 0.0, 0.0, width, height)):
        cells.append(Cell(name, name, 1, x, y, w, h, weight))
        if name in (ROOT_FILES, ELLIPSIS):
            continue
        inset = min(label_inset, h / 2)
        level2 = _join_small(groups[name], total)
        for (sub, sw), (sx, sy, sw_, sh) in zip(level2, _strips(level2, x, y + inset, w, h - inset)):
            cells.append(Cell(f"{name}/{sub}", sub, 2, sx, sy, sw_, sh, sw))
    return cells


def _join_small(named, total):
    """Return (name, weight) pairs in name order; small ones joined into a last `…`."""
    kept, joined = [], 0
    for name in sorted(named):
        if named[name] < MIN_SHARE * total:
            joined += named[name]
        else:
            kept.append((name, named[name]))
    if joined:
        kept.append((ELLIPSIS, joined))
    return kept


def _strips(items, x, y, width, height):
    """Ordered strip layout of (name, weight) items; returns one (x, y, w, h) per item.

    Greedy rule: an item joins the current strip unless it makes the strip's mean
    aspect ratio worse. Last-strip rule: the greedy rule cannot see that the item
    that starts a strip may be its only one, so the last strip joins the strip
    before it when that lowers the mean aspect ratio of their items.
    """
    total = sum(weight for _, weight in items)
    areas = [weight * width * height / total for _, weight in items]
    strips, current = [], []
    for a in areas:
        if current and _mean_aspect(current + [a], width) > _mean_aspect(current, width):
            strips.append(current)
            current = []
        current.append(a)
    strips.append(current)
    if len(strips) > 1:
        prev, last = strips[-2], strips[-1]
        apart = _mean_aspect(prev, width) * len(prev) + _mean_aspect(last, width) * len(last)
        if _mean_aspect(prev + last, width) * (len(prev) + len(last)) < apart:
            strips[-2:] = [prev + last]

    all_area = sum(areas)
    rects, done = [], 0.0
    for strip in strips:
        strip_area = sum(strip)
        top = y + height * done / all_area
        done += strip_area
        bottom = y + height * done / all_area
        acc = 0.0
        for a in strip:
            left = x + width * acc / strip_area
            acc += a
            right = x + width * acc / strip_area
            rects.append((left, top, right - left, bottom - top))
    return rects


def _mean_aspect(strip, width):
    """Mean of max(w/h, h/w) over a strip of the given areas that spans `width`."""
    h = sum(strip) / width
    return sum(max(a / h / h, h * h / a) for a in strip) / len(strip)


def locate(cells: list[Cell], rel_path: str) -> Cell | None:
    """Return the deepest cell that covers the file `rel_path`, or None.

    A `…` cell covers every path under its parent that no named sibling covers.
    """
    keys = {c.key: c for c in cells}
    parts = rel_path.split("/")
    top = parts[0] if len(parts) > 1 else ROOT_FILES
    level1 = keys.get(top) or keys.get(ELLIPSIS)
    # Root files and paths in the level-1 `…` cell have no level-2 cell, so
    # both lookups below miss and the level-1 cell is the answer.
    sub = parts[1] if len(parts) > 2 else ROOT_FILES
    return keys.get(f"{top}/{sub}") or keys.get(f"{top}/{ELLIPSIS}") or level1
