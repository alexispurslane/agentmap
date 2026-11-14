# Live tickets and the focus link

User requests of 2026-11-14. This plan follows `ticket-bar.md`.

## The problem

The ticket bar trusts the tk status. When an agent starts a ticket, it sets the ticket to `in_progress`. It seldom sets the status back. On 2026-11-14 in tertius:

- 34 of the 38 tickets in progress had no live agent and no change for days.
- The 4 tickets that agents worked on were `open`, or they were in a scratch clone.
- The board did not read that clone's folder, `/tmp/t7-3e22/takes/x/.tickets`.

So the board showed old claims, and it did not show the work.

## The model

- Agent threads cause the work. The tk status is a claim that a ticket stores.
- A live ticket is a ticket that a shown thread names (`ticket_agents`), or a ticket that changed in the last 24 hours.
- A stale claim is a ticket in progress that is not live.
- Every mark on the page belongs to a set of threads. A dot, a route, an agent row and an event belong to one thread. An epic column, a ticket row and a ticket badge belong to the threads that work on the ticket or the group.

## The focus link

The page has one focus. The signal `$focus` holds a set of thread tags. It is a string with a space before and after each tag, for example `" A2 A2.3 "`. The signal `$hue` holds the hue of one thread.

- A hover on a mark sets `$focus` to the mark's threads and `$hue` to the hue of the first one. Mouseleave clears `$focus`.
- A mark has the class `hl` while one of its threads is in `$focus`.
- A thread mark lights in its own hue. A ticket mark lights in `$hue`, which `<body>` passes to CSS as `--focus-hue`.
- So a hover on an agent lights the epics and tickets that it works on, in its color. A hover on an epic or a ticket lights its agents, each in its own color.

This replaces `$hover`, which held one tag.

## Ticket checkouts

The T3 reader already maps a path in another checkout to the project by path anchors. It also records the root of that checkout: the part of the path before the anchor. The ticket store reads `<root>/.tickets` of each such checkout that exists. The newest file of an id wins, as before.

## The hover box and the pinned box

- A hover on a ticket mark opens the hover box with the title and the description (`/peek/<id>`), as before.
- A click on a ticket mark pins the box (`/pin/<id>`): the box then also shows the notes, newest first, takes pointer events, and scrolls. It stays at the clicked mark: the mark gets the class `pinned`, which makes it the CSS anchor `--pin`. A click on the same mark, the close button, or Escape unpins it. While the box is pinned, a hover does not change it.
- Each ticket mark has an id that is unique on the page, because one ticket can show in several places.
- The inline text box of a ticket row, the signal `$ticket` and the route `/ticket/<id>` go away.

## Ticket badges on agent rows

- An agent row shows a badge for each ticket that its title names, in a fixed place before the state.
- The title drops the ids that are badges, and the punctuation that they leave, for example `ter-icn3: Unity-guided research` becomes `Unity-guided research`, and `Mottle (t7-3e22)` becomes `Mottle`.
- A badge is a ticket mark: hover, focus and pin work as on a ticket row.

## The ticket list in tiers

A group's list puts the most relevant tier at the bottom, next to the epic strip:

1. (bottom) An agent works on it.
2. In progress and changed in the last 24 hours.
3. Ready.
4. Blocked.
5. (top) One folded line: "N stale claims". A click opens it.

A row in tiers 1 and 2 shows the path of parent ids from the group's root to the ticket. The list opens scrolled to the bottom.

The epic strip shows each group with a live ticket or a stale claim. Groups with more agents come first. A column counts live tickets, ready, blocked, stale claims and closed. Its short list shows its live tickets.
