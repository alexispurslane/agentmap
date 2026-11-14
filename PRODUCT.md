# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One developer who runs many Claude Code and Codex agent threads at once in T3 Code, on one project. The developer works on the main monitor and keeps agentmap open on a portrait side monitor of about 1080 × 1920 CSS pixels. The room has bright daylight during most work.

The developer uses the dashboard in two ways, equally often:

- A glance of one or two seconds, to see whether something moved, finished, or needs an answer.
- A short read of 10 to 30 seconds, to see what one agent is doing and how far its tickets are.

## Product Purpose

agentmap shows, live, what every agent thread of one project does now, where in the project it works, and which thread started which. Agents can also post a status line through MCP, and the project's `tk` tickets show what the work is for. Success: the developer knows the state of all agents without opening their threads.

## Positioning

agentmap joins three views of the same threads that neither Claude Code nor Codex shows together: place (a map of the project's directories), ownership (orchestrators and their subagents), and intent (the project's `tk` tickets, grouped by epic).

## Operating Context

- T3 Code runs the threads. agentmap reads T3 Code's database read-only and polls it every 250 ms.
- The page is a live HTML page. The server sends the whole app as Datastar HTML fragments over Server-Sent Events, and Datastar morphs the changes in.
- The page is watched, not operated. Its only interaction is the hover link between a thread's map dot and its card.

## Capabilities and Constraints

- Per thread: a tag (A, A1, A1.2), a title, the provider and model, and a state (working, needs you, idle, failed).
- Also per thread: an age, the current action ("now line"), the newest thought, the first sentence of its last message, and up to 8 recently touched files.
- Per ticket group: the epic's title and counts, and its unfinished tickets with their metadata and rendered text. See `docs/plans/ticket-bar.md`.
- The map is an ordered strip treemap of the project's directories, two levels deep, sized by lines of code.
- The backend uses the Python 3 standard library and a vendored Mustache engine. The frontend is Datastar 1.0.5 and one CSS file, with no build step.
- The structure, information, and functionality must stay. A redesign changes how they look and read.

## Change tracking

The developer asked that changes be easy to track, in two forms:

- Recent motion: what moved in the last few minutes is visible at a glance, and it fades with age.
- A running log: a short, time-ordered list of events, such as a thread that started, finished a step, or asked a question.

The running log is not built yet. It needs the server to derive events from consecutive snapshots.

## Evidence on Hand

Real data comes from the user's T3 Code database. The tertius project is the usual live example. No screenshots or brand assets exist.

## Product Principles

- Glance first, then read: the most important change must be visible from across the desk.
- Every visual mark encodes a fact: place, owner, state, or age.
- Change is the signal: what is new or recent outranks what is steady.
- Stable positions: a thread keeps its place on the map and in the tree until its facts change.
