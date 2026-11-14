# Ticket bar

The ticket bar replaces the plans board. It shows the project's `tk` tickets, grouped by epic, at the bottom of the page. A click on an epic replaces the event log with that epic's ticket list. A click on a ticket opens its rendered text in place.

The user decided these points on 2026-11-14:

- Plans go away. The MCP server keeps only `set_status`, because the status line feeds the summary line of an agent row.
- Tickets come from the project root and from every worktree of the project. For each ticket id, the newest file wins.
- The ticket text renders through a small built-in Markdown subset, not a vendored library.
- The layout: epic columns side by side in the bottom panel, which scrolls sideways. The ticket list takes the place of the event log until the user closes it. One ticket is open at a time.

## Terms

- Ticket: one `.tickets/<id>.md` file that `tk` writes. It has a front matter block, a `# title` line, a body, and an optional `## Notes` section.
- Ticket folder: a `.tickets` folder in the project root or in one of its worktrees.
- Group: an epic (the group's root) and the tickets whose nearest ancestor of type `epic` is that epic. An inner epic is a member of its parent epic's group, and it heads its own group too. Tickets that are not epics and have no epic above them form one group without a root, shown as "No epic".
- Member: a ticket of a group that is not closed. The root is not a member of its own group.
- Active group: a group with at least one member in progress.
- Ready ticket: a ticket whose dependencies are all closed or unknown. A blocked ticket has at least one dependency that is open or in progress.

## Ticket file format

`tk` writes this format:

```
---
id: ter-69s8
status: in_progress
deps: [ter-9nrc]
links: []
created: 2026-11-04T13:42:25Z
type: feature
priority: 1
assignee: Alexis Purslane
parent: ter-89xq
tags: [rendering, performance, lights]
---
# Per-tile light budget: ...

Body text, which can hold its own `## Design` or `## Acceptance` sections.

## Notes

**2026-11-04T14:17:19Z**

Note text.
```

- The front matter has one `key: value` pair per line. A value in square brackets is a list with comma separators.
- `status` is `open`, `in_progress` or `closed`. `priority` is 0 (highest) to 4.
- `tk` writes most changes through a temporary file and a rename. `tk add-note` appends to the file in place.

## agentmap/tickets.py

```python
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

def parse_ticket(text: str, changed_at: datetime) -> Ticket | None: ...

class TicketStore:
    def __init__(self, folders: Callable[[], list[Path]], *, per_poll: int = 2): ...
    def poll(self) -> bool: ...              # True if any ticket changed
    def tickets(self) -> dict[str, Ticket]: ...

@dataclass(frozen=True)
class Group:
    root: Ticket | None                      # None for "No epic"
    members: tuple[Ticket, ...]              # in display order
    closed: int                              # closed descendants

def groups(tickets: Mapping[str, Ticket]) -> list[Group]: ...   # active groups only
def blocked_by(ticket: Ticket, tickets: Mapping[str, Ticket]) -> tuple[str, ...]: ...
```

- `parse_ticket` returns None for a file without front matter or without an `id`.
- `TicketStore.poll` stats every ticket folder. It rescans a folder whose modification time moved, the first folder (the project root) on every poll, and the next `per_poll` other folders in turn. A rescan reads only the files whose modification time moved. For each id, the ticket with the newest `changed_at` wins. A ticket whose last file disappears drops out.
- `groups` orders the members: in progress, then ready, then blocked. Within each part, it orders by priority, then by `created`. It orders the groups by the root's `created`, oldest first, so that columns keep their places. The "No epic" group comes last.
- The search for an epic above a ticket ends at a missing parent, or where the parent chain repeats.
- The first design grouped tickets by their top ancestor. On tertius, that folded every ticket in progress into one epic with 122 unfinished tickets, because all work nests under `ter-89xq`. The nearest epic gives one column per epic that has work in progress.

## agentmap/markdown.py

```python
def render(text: str) -> str: ...   # HTML, safe to insert
```

- It escapes all text first. It produces no raw HTML from the input.
- Blocks: fenced code blocks, headings, unordered lists (`-` or `*`), ordered lists (`1.`) and paragraphs.
- Headings `#` to `######` are drawn as `h4` to `h6`.
- Inline: code spans, `**bold**`, `*emphasis*` and `_emphasis_`, and links. A link keeps its href only for `http` and `https` URLs.

## Agents and tickets

If a thread's title or its current status line names a ticket's id, the thread works on that ticket. The MCP instructions ask agents to start their status line with the ticket id. A group shows the tags of the threads that work on any of its members. The rule lives in `components/view.py`.

## Page and stream

- The `<body>` holds three fragments: `#app` (rail, map, agent tree, event log), `#epics` (the bottom strip) and `#tickets` (the ticket lists). The server patches each one by its id. If a fragment's HTML did not change, the server does not send it.
- `#app` and `#mid` use `display: contents`, so all parts share one page grid. `#tickets` sits in the grid cell of the event log.
- Signals on `<body>`: `focus` and `hue` (see `live-tickets.md`), `epic` (the open group's root id, or `''`), `peek`, `pin` and `pinat` (the hover box and the pinned box). A click on an epic column sets `$epic`. The event log shows while `$epic` is `''`.
- `#tickets` holds one list per active group, each shown only while `$epic` names it.
- A ticket's text loads on demand, because the unfinished tickets of one tertius epic hold 434 KiB of text. A hover loads the hover box from `/peek/<id>`. A click pins the box and loads the whole ticket from `/pin/<id>`. See `live-tickets.md`, which replaced the inline text box of a row.
- A ticket row shows one line of metadata, then the ticket's title on up to two lines. It shows no start of the ticket's text.
- A hover box shows a ticket's title and rendered description, without its notes, as on Wikipedia. A hover on a ticket row or on a ticket in an epic column sets the signal `$peek` and sends `@get('/peek/<id>')`. The answer patches the one `<aside id="peek">` on the page with mode `inner`. The box is outside the three fragments, so a stream patch keeps it. Its content shows only while `$peek` names its ticket, so a late answer for a ticket that the pointer left stays hidden. The box opens after 0.35 s, and it stays closed for the open ticket.
- CSS anchor positioning places the box. The element under the pointer (`:hover`) is the anchor, because one ticket can show in its epic column and in the open list at the same time. The box goes below the anchor, or above it or to its left when it does not fit.

## Removed

- `plan.py`, the plan board, the plan card, and the MCP tools `set_goal`, `add_todos`, `mark_todo` and `get_plan`.
- `set_status` moves to `agentmap/status.py`, a log of status lines at `.agentmap/status.jsonl`.
