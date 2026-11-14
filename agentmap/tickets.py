"""The project's `tk` tickets: the parser, the store that follows the files, and the groups.

A ticket is one `.tickets/<id>.md` file that `tk` writes: a front matter block,
a `# title` line, a body and an optional `## Notes` section. A ticket folder is
a `.tickets` folder in the project root or in one of its worktrees. The same id
can have a file in several folders. The newest file wins.

A group is a top ancestor in the `parent` chain and all its descendants.
Tickets without a parent and without children form one group without a root,
"No epic". A member of a group is a descendant of its root that is not closed.
"""

from __future__ import annotations

import functools
import os
import re
import stat
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmap.markdown import fenced_blocks

# Statuses that keep a dependent ticket waiting.
OPEN = frozenset({"open", "in_progress"})
DEFAULT_PRIORITY = 2
NOTES_HEADING = "## Notes"

_TITLE = re.compile(r"#[ \t]+(.*)")
_TIMESTAMP = re.compile(r"\*\*(\d{4}-\d\d-\d\dT[^*]+)\*\*")


@dataclass(frozen=True)
class Note:
    at: datetime          # UTC, from the **timestamp** line
    text: str             # Markdown


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    status: str                  # open | in_progress | closed
    type: str                    # task | bug | feature | epic | chore
    priority: int                # 0..4; 2 when missing or not a number
    tags: tuple[str, ...]
    deps: tuple[str, ...]
    parent: str | None
    created: datetime | None     # UTC
    changed_at: datetime         # the file's modification time, UTC
    body: str                    # Markdown between the title and "## Notes"
    notes: tuple[Note, ...]      # oldest first


@dataclass(frozen=True)
class Group:
    root: Ticket | None                      # None for "No epic"
    members: tuple[Ticket, ...]              # in display order
    closed: int                              # closed descendants


def parse_ticket(text: str, changed_at: datetime) -> Ticket | None:
    """The ticket of one file's text, or None for a file without front matter or id.

    A front matter that never closes (a file that is still being written) counts
    as no front matter. A ticket without a `# title` line takes its id as title.

    The notes section starts at the first `## Notes` line outside a fenced code
    block. Each `**<ISO time>**` line in it, outside a fenced block, starts a
    note. Text in the section before the first such line is not a note: it stays
    at the end of the body, under its `## Notes` heading, so that nothing is lost.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    fields: defaultdict[str, str] = defaultdict(str)  # A missing field reads as "".
    for line in lines[1:end]:
        key, colon, value = line.partition(":")
        if colon:
            fields[key.strip()] = value.strip()
    if not fields["id"]:
        return None
    rest = lines[end + 1:]
    start = next((i for i, line in enumerate(rest) if line.strip()), len(rest))
    title = _TITLE.fullmatch(rest[start]) if start < len(rest) else None
    if title:
        rest = rest[start + 1:]
    body, notes = _split_notes(rest)
    return Ticket(
        id=fields["id"],
        title=title[1].strip() if title else fields["id"],
        status=fields["status"] or "open",
        type=fields["type"] or "task",
        priority=_priority(fields["priority"]),
        tags=_list(fields["tags"]),
        deps=_list(fields["deps"]),
        parent=fields["parent"] or None,
        created=_time_of(fields["created"]),
        changed_at=changed_at,
        body=body,
        notes=notes)


def _split_notes(lines: list[str]) -> tuple[str, tuple[Note, ...]]:
    """The body text and the notes of the lines after the title."""
    heading = next((i for i in _outside_fences(lines) if lines[i].strip() == NOTES_HEADING),
                   None)
    if heading is None:
        return _text(lines), ()
    section = lines[heading + 1:]
    stamps = [(i, at) for i in _outside_fences(section)
              if (m := _TIMESTAMP.fullmatch(section[i].strip())) and (at := _time_of(m[1]))]
    first = stamps[0][0] if stamps else len(section)
    loose = _text(section[:first])
    body = _text(lines[:heading])
    if loose:
        body = _text([body, "", NOTES_HEADING, "", loose])
    ends = [i for i, _ in stamps[1:]] + [len(section)]
    notes = [Note(at, _text(section[i + 1:end])) for (i, at), end in zip(stamps, ends)]
    return body, tuple(sorted(notes, key=lambda n: n.at))


def _outside_fences(lines: list[str]) -> list[int]:
    """The indexes of the lines that are not part of a fenced code block."""
    inside: set[int] = set()
    for opening, closing in fenced_blocks(lines):
        inside.update(range(opening, len(lines) if closing is None else closing + 1))
    return [i for i in range(len(lines)) if i not in inside]


def _text(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def _priority(value: str) -> int:
    try:
        priority = int(value)
    except ValueError:
        return DEFAULT_PRIORITY
    return priority if 0 <= priority <= 4 else DEFAULT_PRIORITY


def _list(value: str) -> tuple[str, ...]:
    """The items of a `[a, b]` value. A value without brackets is one item."""
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return tuple(item for item in (part.strip() for part in value.split(",")) if item)


def _time_of(value: str) -> datetime | None:
    """An ISO time as aware UTC, or None. A time without a zone is UTC."""
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _file_time(mtime_ns: int) -> datetime:
    """A file modification time in nanoseconds as aware UTC, exact to the microsecond."""
    seconds, ns = divmod(mtime_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc) + timedelta(microseconds=ns // 1000)


# A cached file: ((modification time in ns, size in bytes), its ticket or None).
_File = tuple[tuple[int, int], Ticket | None]


@dataclass
class _Folder:
    """What the store keeps of one ticket folder."""

    mtime: int                   # the folder's st_mtime_ns, taken before its last scan
    files: dict[str, _File]      # by file name in sorted order, the `.md` files that the scan read


class TicketStore:
    """The tickets of a list of ticket folders, kept in step with the files on disk.

    `folders` gives the ticket folders on each poll. The first one is the project
    root. A folder that does not exist, or that cannot be listed, has no tickets.

    `poll` stats every folder. It rescans a folder whose modification time moved,
    the first folder on every poll, and the next `per_poll` other folders in turn.
    The turns catch `tk add-note`, which appends to a file in place and so does
    not move the folder's time. A rescan reads only the files whose modification
    time or size moved. The folder's time is taken before the scan, so a file
    that arrives during a scan moves the time again for the next poll.

    Invariants (`check_invariants` checks them all):

    1. Every cached folder is in the folder list of the last poll.
    2. The turn index is inside the other folders of the last poll (0 when none).
    3. Every cached ticket's `changed_at` is the modification time of its file's key.
    4. `tickets()` equals the merge of the cached tickets: for each id, the ticket
       with the newest `changed_at`. A tie goes to the earlier folder of the list,
       then to the first file name.
    """

    def __init__(self, folders: Callable[[], list[Path]], *, per_poll: int = 2):
        if per_poll < 0:
            raise ValueError("per_poll must not be negative")
        self._list_folders = folders
        self._per_poll = per_poll
        self._listed: tuple[Path, ...] = ()
        self._folders: dict[Path, _Folder] = {}
        self._turn = 0
        self._tickets: dict[str, Ticket] = {}

    def poll(self) -> bool:
        """Follows the files on disk. True if any ticket changed."""
        listed = tuple(dict.fromkeys(Path(f) for f in self._list_folders()))
        due = self._take_turn(listed[1:])
        dirty = False
        for gone in self._folders.keys() - set(listed):
            del self._folders[gone]
            dirty = True
        for i, folder in enumerate(listed):
            dirty |= self._refresh(folder, i == 0 or folder in due)
        self._listed = listed
        assert self._folders.keys() <= set(listed)
        if not dirty:
            return False
        merged = self._merge()
        changed = merged != self._tickets
        self._tickets = merged
        return changed

    def tickets(self) -> dict[str, Ticket]:
        """The tickets by id, as of the last poll. The dict is the caller's own copy."""
        return dict(self._tickets)

    def check_invariants(self) -> None:
        """Raises AssertionError if a cache is out of step. See the class docstring."""
        others = len(self._listed) - 1
        _require(self._folders.keys() <= set(self._listed), "a cached folder is not listed")
        _require(0 <= self._turn < max(1, others), "the turn is outside the other folders")
        _require(all(t is None or t.changed_at == _file_time(key[0])
                     for folder in self._folders.values() for key, t in folder.files.values()),
                 "a cached ticket's time is not its file's time")
        _require(self._tickets == self._merge(), "the tickets are not the merge of the files")

    def _take_turn(self, others: tuple[Path, ...]) -> set[Path]:
        """The next `per_poll` other folders, at most each once; moves the turn on."""
        if not others:
            self._turn = 0
            return set()
        start = self._turn % len(others)
        count = min(self._per_poll, len(others))
        self._turn = (start + count) % len(others)
        return {others[(start + k) % len(others)] for k in range(count)}

    def _refresh(self, folder: Path, due: bool) -> bool:
        """Rescans the folder if it is due or its time moved. True if its files changed."""
        try:
            mtime = os.stat(folder).st_mtime_ns
        except OSError:
            return self._folders.pop(folder, None) is not None
        cached = self._folders.get(folder)
        if cached is not None and cached.mtime == mtime and not due:
            return False
        before = cached.files if cached is not None else {}
        try:
            with os.scandir(folder) as entries:
                found = sorted((e for e in entries if e.name.endswith(".md")),
                               key=lambda e: e.name)
        except OSError:
            return self._folders.pop(folder, None) is not None
        files: dict[str, _File] = {}
        for entry in found:
            try:
                st = entry.stat()
            except OSError:  # The file went away after the listing.
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            key = (st.st_mtime_ns, st.st_size)
            old = before.get(entry.name)
            if old is not None and old[0] == key:
                files[entry.name] = old
                continue
            try:
                with open(entry.path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:  # Left out, so that the next scan tries again.
                continue
            files[entry.name] = (key, _parse_file(text, _file_time(st.st_mtime_ns)))
        self._folders[folder] = _Folder(mtime, files)
        return files != before

    def _merge(self) -> dict[str, Ticket]:
        """For each id, the cached ticket with the newest `changed_at`."""
        best: dict[str, Ticket] = {}
        for folder in self._listed:
            cached = self._folders.get(folder)
            if cached is None:
                continue
            for _, t in cached.files.values():  # In order of file name.
                if t is not None and (t.id not in best or t.changed_at > best[t.id].changed_at):
                    best[t.id] = t
        return best


# How many distinct file texts `_parse_once` keeps. Every worktree holds a copy of the
# root's tickets, so most files share their text with a file in another folder.
PARSE_CACHE = 4096


@functools.lru_cache(maxsize=PARSE_CACHE)
def _parse_once(text: str) -> Ticket | None:
    return parse_ticket(text, _NEVER)


def _parse_file(text: str, changed_at: datetime) -> Ticket | None:
    """parse_ticket, which parses each distinct text once."""
    ticket = _parse_once(text)
    return None if ticket is None else replace(ticket, changed_at=changed_at)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def blocked_by(ticket: Ticket, tickets: Mapping[str, Ticket]) -> tuple[str, ...]:
    """The dependencies of the ticket that are open or in progress, in the ticket's order."""
    return tuple(d for d in ticket.deps if d in tickets and tickets[d].status in OPEN)


def groups(tickets: Mapping[str, Ticket]) -> list[Group]:
    """The active groups: those with at least one member in progress.

    Each ticket belongs to the group of its nearest ancestor of type epic. An
    inner epic is thus a member of its parent epic's group, and it heads its
    own group too. A ticket that is not an epic and has no epic above it
    belongs to "No epic". An epic with no epic above it belongs to no group.

    Groups come in order of their root's `created`, oldest first (a root without
    it last, ties by root id), and "No epic" comes last.
    """
    found: dict[str | None, list[Ticket]] = {}
    for t in tickets.values():
        epic = _epic_above(t, tickets)
        if epic is not None or t.type != "epic":
            found.setdefault(epic, []).append(t)
    result = []
    for root, belonging in found.items():
        members = [t for t in belonging if t.status != "closed"]
        if any(t.status == "in_progress" for t in members):
            members.sort(key=lambda t: _member_key(t, tickets))
            result.append(Group(tickets[root] if root is not None else None, tuple(members),
                                len(belonging) - len(members)))
    result.sort(key=lambda g: (g.root is None, _dated(g.root) if g.root else ()))
    return result


def _epic_above(ticket: Ticket, tickets: Mapping[str, Ticket]) -> str | None:
    """The id of the nearest ancestor of type epic, or None.

    The search ends at a missing parent, or where the parent chain repeats.
    """
    seen = {ticket.id}
    while ticket.parent in tickets and ticket.parent not in seen:
        ticket = tickets[ticket.parent]
        if ticket.type == "epic":
            return ticket.id
        seen.add(ticket.id)
    return None


def _member_key(t: Ticket, tickets: Mapping[str, Ticket]) -> tuple:
    """In progress, then ready, then blocked; then priority, creation and id."""
    part = 0 if t.status == "in_progress" else 2 if blocked_by(t, tickets) else 1
    return (part, t.priority, *_dated(t))


_NEVER = datetime.min.replace(tzinfo=timezone.utc)


def _dated(t: Ticket) -> tuple:
    """A sort key by creation time, undated last, then by id."""
    return (t.created is None, t.created or _NEVER, t.id)
