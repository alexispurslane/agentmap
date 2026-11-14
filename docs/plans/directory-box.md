# The directory box

User request of 2026-11-14. This plan follows `live-tickets.md`, which defines the focus link and the box.

## The request

A hover on a directory cell of the map opens a box. The box shows the files in the cell that agents edited most recently. After each file come the tags of the agents that edited it.

## The model

- The page has one box. A box has a subject: a ticket or a map cell. A subject is a kind (`t` for a ticket, `c` for a cell) and an id (the ticket id or the cell key).
- The key of a subject is the element id of the subject, `element_id(kind, id)`. The signals `$peek` and `$pin` hold such keys.
- A mark of a subject opens the box on hover and pins it on click, as before. The server routes are `/peek/<kind>/<id>` and `/pin/<kind>/<id>`.
- A cell is a mark. Its threads are the threads that edited a file in the cell inside the event window. So a hover on a cell lights those agents, and a hover on an agent lights the cells that it edited, in its color.
- A directory cell with subdirectory cells is a parent. It lies under its subdirectory cells, so a lit parent colors only its label. Any other lit cell takes the focus hue as its background.

## Which files are in a cell

If `locate` gives a cell for the file's path, the file is in that cell. If `locate` gives a subdirectory cell, the file is also in the cell of its directory. So a hover on the label band of `src` shows the edits in all of `src`.

## The box content

- The title is the cell's directory, for example `src/Tertius.Rendering/`.
- Each line is one file: its path inside the cell's directory, the age of its newest edit, and the tags of the threads that edited it, newest editor first.
- The newest file comes first. A hover box shows 8 files. A pinned box shows 40 files and scrolls.
- The data are the `edits` events of the event log. A thread that is not in the snapshot has no tag, and its edits do not show.
- Below the edits, after a rule and the heading "Largest files by compressed size", every box lists the 5 heaviest files of the cell, with their map weight in place of an age. The map weight is the gzip size, with prose at a quarter and data at a half. Binary files weigh 0 and do not show. A file can show in both lists.
- A cell without edits says "No agent edited a file here in the event window." above that list.

## The anchor

The box opens under the cell's label, because a cell can be as tall as the map. The label of the hovered cell is the CSS anchor `--peek`. The label of the pinned cell is the anchor `--pin`.
