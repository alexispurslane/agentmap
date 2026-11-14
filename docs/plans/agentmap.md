# agentmap: a live dashboard of agent threads

agentmap is a dashboard for a portrait side monitor. It shows what every Claude Code and Codex thread in one T3 Code project does now. It shows each thread in two ways:

- In space: a dot on a map of the project, in the area of the files that the thread works on.
- In the ownership tree: a card under the card of the thread that started it.

Agents can also post a status line through a small MCP server in the same process. The bottom of the page shows the project's `tk` tickets by epic.

## Decisions

The user chose these points on 2026-11-14:

| Point | Decision |
|---|---|
| Where space shows | One shared map over the top part of the screen, with one dot per thread. |
| Map shape | A treemap: each directory gets an area in proportion to its code size. |
| Where the status log is stored | In the watched project's repository, in `.agentmap/status.jsonl`. |
| How a Codex caller is identified | By the `X-T3-Thread` header if it is there, else by T3 Code's record of the call (see `caller.py`). |

Other fixed points:

- The backend uses the Python 3.14 standard library and one vendored package: the Mustache engine chevron 0.14.0, in `agentmap/vendor/chevron`. Test tools (coverage, pytest, mutmut) come from the Nix dev shell.
- Plans (goal and todo list through MCP) were removed on 2026-11-14. The project's `tk` tickets show intent instead. See `docs/plans/ticket-bar.md`.
- The frontend is Datastar 1.0.5, vendored in `agentmap/static/datastar.js`. The server sends HTML fragments. The page has no other script.
- One server process watches one project. It serves the page, the event stream and the MCP endpoint on one port.

## Terms

- Thread: one T3 Code thread. A subagent started by `delegate_task` or by Claude's Agent tool is also a thread, with a parent.
- Root thread: a thread with no parent.
- Status line: one line that a thread posts through MCP about what it does or waits for now.
- Now line: one line of text that tells what the thread does at this moment, for example `Editing src/render/cull.rs`.
- Cell: one rectangle of the treemap. A cell stands for one directory, or for the files directly in one directory.
- Touched path: a file path, relative to the project root, that a thread read or changed.

## Data flow

```
T3 Code statev2.sqlite --(read-only, poll)--> t3.py  --\
project files (git ls-files) --> treemap.py ------------>  components/ --> SSE /stream --> Datastar page
.agentmap/status.jsonl <--> status.py <-- mcp.py <-- POST /mcp <-- agents
```

The poller thread in `server.py` is the only caller of `t3.py` and of the components. It renders the whole `<main id="app">` element. If the HTML differs from the last HTML, it publishes it to every open stream. Datastar morphs the new element into the page, so the DOM changes only where the HTML changed.

## Module contracts

All modules live in the package `agentmap/`. Tests live in `tests/` and use `unittest`. Each module below has one owner ticket.

### status.py: the status store

```python
class StatusError(ValueError): ...     # a refused status; the message is shown to the agent
@dataclass(frozen=True)
class Status:
    text: str
    at: datetime                       # UTC, the `ts` of the log record
class StatusStore:
    def __init__(self, log_path: pathlib.Path): ...
    def set_status(self, thread: str, text: str) -> None: ...
    def status(self, thread: str) -> Status | None: ...
    def statuses(self) -> dict[str, Status]: ...
    def events(self) -> list[Event]: ...     # newest first, at most 200
    def check_invariants(self) -> None: ...
```

- The source of truth is an append-only log, one JSON object per line: `{"ts", "thread", "op": "status", "text"}`. The memory is the fold of the log.
- A text is stripped. An empty text, a text over 160 characters, and a text that is not a string raise `StatusError`.
- A new status replaces the thread's old one. A new text makes a `status` event. The same text again renews the time but makes no event.
- A replay skips records of other kinds, such as the old plan records, and warns about lines that are not valid.
- Invariants: a fresh replay of the log gives the same statuses and the same events as the memory.

### mcp.py: the MCP endpoint

`mcp.py` implements the smallest part of the MCP Streamable HTTP transport that Claude Code and Codex need. It has no network code. The server calls one function:

```python
def handle(body: bytes, headers: Mapping[str, str], store: StatusStore,
           thread_project: Callable[[str], bool | None]) -> tuple[int, dict[str, str], bytes]: ...
```

where

- `body` = the raw POST body.
- `headers` = the request headers, with case-insensitive lookup.
- `thread_project(thread_id)` = a function that tells which project a thread belongs to. It gives one of three values:
  - `True`: the thread is in the watched project.
  - `False`: the thread is in another project.
  - `None`: T3 Code does not know the thread yet.
- The return value is the HTTP status, the response headers and the response body.

Behavior:

- The body is one JSON-RPC 2.0 message. A batch (a JSON array) gets error `-32600`.
- `initialize`: the result has `protocolVersion`, `capabilities: {"tools": {}}`, `serverInfo` and `instructions`.
  - For `protocolVersion`, the server supports `2025-06-18`, `2025-03-26` and `2024-11-05`. For a supported client version, the server answers with that version. For another version, the server answers with `2025-06-18`.
  - The `instructions` text tells the agent to call `set_status` after each major step, and to start the line with the `tk` ticket id that it works on.
- `ping`: an empty result.
- `tools/list`: the one tool below with its JSON Schema input.
- `tools/call`: runs the tool. A `StatusError` becomes a result with `isError: true` and the message as text. A success returns `Status posted: <text>`. Another tool name gets error `-32602`.
- A message without `id` is a notification. The answer is status 202 with an empty body.
- An unknown method gets error `-32601`. Bad JSON gets error `-32700`.
- The thread id comes from the header `X-T3-Thread`. Three header values are bad: no header, an empty header, and the literal text `${T3CODE_THREAD_ID}`.
- For a bad header, `handle` calls `find_caller(tool, arguments)`, its optional fifth argument. That function names the thread or raises `CallerError`. A `CallerError` becomes a tool result with `isError: true` and the error's message.
- For a bad header without `find_caller`, a tool call returns `isError: true`. The message tells how to set the header.
- When `thread_project` returns `False`, the tool call returns `isError: true`.
- The server checks the `Origin` header before it calls `handle`. That rule is in `server.py`.

Tools:

| Tool | Input | Effect |
|---|---|---|
| `set_status` | `status: string` | Sets one line about what the thread does or waits for now. |

### caller.py: the thread of a call without a header

All Codex threads in T3 Code share one `codex app-server` process. That process has no `T3CODE_THREAD_ID`, so Codex cannot send the header. T3 Code records each MCP call as a `dynamic_tool` turn item, with the thread, the tool name and the arguments. The item is active while the call runs.

```python
class CallerError(Exception): ...
class CallerFinder:
    def __init__(self, lookup, *, timeout=2.0, interval=0.05, clock=time.monotonic, sleep=time.sleep): ...
    def __call__(self, tool: str, arguments: dict) -> str: ...   # the thread id
```

where

- `lookup(tool, arguments)` = `T3Reader.find_tool_calls` with the current time. It returns the active, matching records as (turn item id, thread id) pairs, oldest first.
- `timeout` = how long, in seconds, the finder waits for T3 Code to write the record.
- `interval` = the time, in seconds, between two lookups.

Rules:

- One record serves one request. The finder claims each record that it uses, and it keeps a claim for 600 seconds. The lookup drops records that started more than 5 minutes ago. Thus a record cannot match again after its claim ends.
- When records of two different threads match, the finder raises `CallerError`. The message asks the agent to call again.
- When no record matches before the timeout, the finder raises `CallerError`. The message tells Claude Code users to set the header.

### t3.py: the T3 Code reader

`t3.py` reads T3 Code's database read-only and builds one snapshot of the watched project's threads.

```python
class T3Reader:
    def __init__(self, db_path: pathlib.Path, project_root: pathlib.Path, window: timedelta): ...
    def changed(self) -> bool: ...                 # True if PRAGMA data_version moved since the last call
    def snapshot(self, now: datetime) -> list[AgentThread]: ...
    def thread_project(self, thread_id: str) -> bool | None: ...
    def find_tool_calls(self, tool: str, arguments: dict, now: datetime) -> list[tuple[str, str]]: ...
    def events(self, now: datetime) -> list[Event]: ...   # newest first, at most 80
    def worktrees(self) -> list[str]: ...          # sorted
```

`worktrees` gives the distinct `worktreePath` values of the project's threads that are folders on disk. Every thread that is not deleted counts, whatever its age. An archived thread counts too, because its worktree can still hold tickets.

`find_tool_calls` reads `dynamic_tool` items with the status `pending`, `running` or `waiting`. It keeps an item when all of these are true:

- The tool name is `mcp__agentmap__<tool>` (Claude Code) or `agentmap.<tool>` (Codex).
- The item's `input` equals `arguments`. A missing input counts as `{}`.
- The item's `startedAt` is at most 5 minutes before `now`.

`AgentThread` is a frozen dataclass:

| Field | Meaning |
|---|---|
| `id` | The T3 Code thread id. |
| `parent_id` | `lineage.parentThreadId`, or `None` for a root thread. |
| `title` | The thread title. |
| `provider` | `"claude"` or `"codex"`. |
| `model` | The model name from `modelSelection.model`, or `None`. |
| `state` | One of `working`, `needs_you`, `idle`, `failed`. |
| `created_at`, `updated_at` | Timezone-aware datetimes. |
| `active_at` | When the thread's agent last acted: the latest `startedAt` or `completedAt` of its newest 50 turn items, or `created_at` for a thread without items. T3 Code also moves `updated_at` without agent work, for example when it settles a thread, so the page uses `active_at` for ages. |
| `now` | The now line, or `None`. |
| `thought` | The first sentence of the newest thinking text, at most 160 characters, or `None`. |
| `last_said` | The first sentence of the newest assistant message, at most 160 characters, or `None`. |
| `thought_at`, `said_at` | The `startedAt` time of the item that `thought` or `last_said` came from. `None` when that text is `None`. |
| `touched` | Up to 8 `Touch(path, at)` values, newest first, without repeated paths. `at` is the `startedAt` time of the newest item that touched the path. |

All times are timezone-aware UTC.

Facts about the database (schema of T3 Code on 2026-11-14):

- `projection_projects(project_id, workspace_root, deleted_at)` maps a project root to project ids. More than one id can have the same root. Use all of them.
- `orchestration_v2_projection_threads` has one row per thread. Its `payload_json` has `lineage.parentThreadId`, `worktreePath`, `modelSelection.model` and `providerInstanceId`. Skip rows with `deleted_at` or `archived_at`.
- `orchestration_v2_projection_runs` has the runs of a thread, with `ordinal` and `status`. The newest run's status gives the state: `queued`, `preparing`, `starting`, `running` and `waiting` mean working. `failed` means failed. Other values mean idle.
- `orchestration_v2_projection_turn_items` has each step of a run. The `type` column and `payload_json` hold the content. A waiting `user_input_request`, `approval_request` or `secret_request` item means `needs_you`.
- A run row has `completed_at` once the run ends.
- The thread payload has `settledAt` and `settledOverride`. T3 Code sets `settledAt` and the override `settled` when the user settles a thread. It does the same for a subagent within a second of the end of its task, also when the task fails.
- Every turn item's payload has `startedAt`. An ended item also has `completedAt`.
- The database is in WAL mode. Open it with `file:...?mode=ro` and `uri=True`.

Which threads a snapshot holds: threads of the project with `updated_at` inside the window, plus all ancestors of those threads, so that every tree is whole.

Which threads the board shows: every thread of the snapshot that is not settled, and every settled thread with a shown descendant. A thread is settled when three facts are true:

1. Its work ended: the newest task from its parent is `completed`, `cancelled` or `interrupted`, or T3 Code marked it settled.
2. It did not fail: its task is not `failed` and its state is not failed. T3 Code marks a failed subagent settled, but the board keeps failures in view.
3. It does nothing now: its state is not working and not `needs_you`.

A root has no task, so only the mark of T3 Code settles it. A settled thread leaves the map and the tree, but its events stay in the log.

Now line rules, from the newest turn item that is `pending`, `running` or `waiting`. If no item is active, the now line is `None`.

| Item | Now line |
|---|---|
| `command_execution` | `Running: ` + the first line of `input`, with a `bash -lc '...'` wrapper removed, at most 100 characters. |
| `file_change` | `Editing ` + the touched path. |
| `dynamic_tool` `Read` | `Reading ` + the touched path. |
| `dynamic_tool` `Grep` or `Glob` | `Searching ` + the pattern. |
| `dynamic_tool` whose name ends in `delegate_task` | `Delegating: ` + the task title. |
| `dynamic_tool` whose name ends in `background_wait` or `t3_thread_wait` | `Waiting on background work`. |
| `dynamic_tool`, another name | `Using ` + the tool name without the `mcp__server__` or `server.` prefix. |
| `web_search` | `Searching the web: ` + the first pattern. |
| `reasoning` | `Thinking`. |
| `assistant_message` | `Writing a reply`. |
| `subagent` | `Waiting on ` + the subagent title. |
| `user_input_request`, `approval_request`, `secret_request` | `Needs your answer`. |

Touched paths come from `file_change.fileName` and from the `file_path`, `path` or `notebook_path` input of `dynamic_tool` items. A path inside the thread's worktree or inside the project root becomes relative to that root.

Agents also edit files in other copies of the project, such as another T3 worktree or a scratch clone under `/tmp`. An anchor is the first two parts of a project file's path, for example `src/render` or `docs/plan.md`. The server's `CellCache` keeps the anchors of the last file listing, and `T3Reader` gets them through its `anchors` argument. For a path outside both roots, the reader looks for the first pair of parts, from the left, that is an anchor. The path in the project starts there. A path without an anchor is not a touched path.

The reader keeps the touched paths of items that it read before. So `__main__` lists the project's files once before the reader's first scan.

Speed: one `snapshot` call must take under 50 ms on the real database with 50 threads in the window. Cache the result per thread. When the newest item of a thread changed, read its turn items again.

Events. `events(now)` reads the threads that `snapshot` holds, ancestors included. It gives the events with a time inside the window and not after `now`, newest first, at most 80. Events at the same time are in a fixed order: by thread id, then kind, then text, all descending.

| Kind | Source | Time | Text |
|---|---|---|---|
| `started` | The thread row. | `created_at` | The thread title. |
| `edits` | A `file_change` item with a touched path. | `startedAt` | The touched path. |
| `asks` | A `user_input_request`, `approval_request` or `secret_request` item. | `startedAt` | The first sentence of the first `questions[].question`, of `prompt`, or of `label`. `""` when there is none. |
| `finished`, `failed` | A run with the status `completed` or `failed`. | `completed_at` | The first sentence of the run's newest assistant message, or `""`. |

A native subagent has no runs. Its parent's `subagent` item stands for its run, as for its state. When that item is `completed` or `failed` and has `completedAt`, the subagent gets a `finished` or `failed` event at that time. The text is the first sentence of the subagent's newest assistant message.

Cache: `events` keeps a log per thread. It reads the thread again only when `updated_at` moved. Then it reads only the items newer than the newest ordinal that it saw, and drops the events that left the window. It reads the end of a native subagent on every call. `now` must not move back between calls. Speed: under 30 ms per call on the real database with a 3-hour window.

### events.py: dated facts

```python
@dataclass(frozen=True)
class Event:
    at: datetime      # timezone-aware UTC
    thread_id: str
    kind: str         # one of KINDS
    text: str         # the detail; "" when there is none
KINDS = ("started", "edits", "asks", "finished", "failed", "status")
```

An event is one dated fact about one thread. The page uses events for its running event log, and it fades each mark by its age. `T3Reader.events` makes the first five kinds. `StatusStore.events` makes `status`.

### treemap.py: the project map

```python
def code_weights(root: pathlib.Path, cache: dict | None = None) -> dict[str, int]: ...   # relative file path -> weight
def build_cells(weights: dict[str, int], width: float, height: float) -> list[Cell]: ...
def locate(cells: list[Cell], rel_path: str) -> Cell | None: ...
```

`Cell` is a frozen dataclass: `Cell(key, label, depth, x, y, w, h, weight)`.

- `code_weights` lists files with `git ls-files -z --cached --others --exclude-standard`. Thus a new file that an agent creates shows before anyone commits it. If the root is not a git repository, it walks the tree and skips hidden directories, `node_modules`, `target` and `build`. A file's weight is its zlib size at level 6, a measure of the information that it holds. Prose files (`.md`, `.markdown`, `.txt`, `.rst`, `.adoc`, `.org`) count a quarter of it, and data files (`.json`, `.log`, `.csv`, `.tsv`, `.jobs`) count a half. A line of prose holds about 3.5 times the compressed bytes of a line of code, so a quarter keeps prose at about the share of the map that line counts gave it. An empty file, and a file with a NUL byte in its first 8 KiB, weigh 0. The `cache` keeps each file's weight by its size and modification time, so a refresh compresses only the files that changed.
- The map has two levels. Level 1 is each top-level directory. All files directly in the root form one cell with the key `.`. Level 2 is each subdirectory of a top-level directory, plus one cell for the files directly in it, with the key `<dir>/.`.
- Order: in each level, cells are sorted by name. The layout is an ordered strip treemap (Bederson, Shneiderman and Wattenberg, 2002). The method keeps the name order. Thus, when sizes change, a directory stays in about the same place. The method was made for ordered data that changes over time, at tens to thousands of items. Our maps have about 10 to 60 cells, so the method fits.
- A level-2 cell under 1.5 % of the map area joins one `…` cell in its parent. A level-1 cell under 1.5 % joins one `…` cell at level 1.
- `locate` returns the deepest cell whose key covers the path. A `…` cell covers the paths of the cells that it joined.
- The server builds the cells at start and again every 10 minutes.

### components/: the HTML

- Each folder `agentmap/components/<name>/` holds `<name>.py`, with one pure function that takes typed arguments and returns HTML, and `<name>.html`, its Mustache template. A template can include another component's template as a partial, for example `{{> hover}}`.
- `components/_template.py` renders a template with chevron. Chevron escapes `{{value}}`. Attributes in templates use double quotes, because chevron does not escape `'`.
- `components/view.py` holds the facts that several components share: the ownership tree (`Forest`), tags, hues, the ink ramp of ages (`a0` to `a4`), the summary line, and the rule that hides settled threads.
- `page` returns the whole document. `app` returns `<main id="app">` with the rail, the map, the agent tree and the event log.
- Each thread gets its own hue, subagents included. In order of creation, each hue is the one before plus the golden angle (137.5°).
- Every mark is linked through the focus: the signals `$focus` (a set of thread tags) and `$hue`. A thread mark (dot, route, row, event, chip) uses `{{> hover}}`. A ticket mark (epic column, ticket, ticket row) uses `{{> link}}`. See `docs/plans/live-tickets.md`.
- Map routes and agent rows fade in real time. The page keeps the Unix time in the signal `$now` and sets it every 10 seconds. The `fade` component gives a mark the attribute `data-style:--fade`. Its value grows from 0 at the mark's time to 1 after an hour, as the square root of the age.
- In the CSS, a route segment and a stop lose opacity at a steady rate (`1 - --fade²`) and are gone after an hour. The newest segment, the one that ends at the dot, starts in the full color of the thread and fades from full opacity in the same way. A hover on a thread draws its whole route at full color.
- An agent row fades from its `active_at`, and its idle age counts from the same time. A row that is working or that needs the user has full ink, because its agent acts now. Its title and summary text mix from full ink to grey with `--fade`. Its tag badge gets a little lighter and less saturated, so that the tags stay readable and distinct.
- The event log, the epic strip and the ticket list still use the ink ramp `a0` to `a4`, which the server sets when it renders.
- Each thread gets a tag: the root gets a letter (`A`, `B`, ...). A child gets its parent's tag plus its position, for example `B2` and `B2.1`.
- An agent row shows its tool line (the now line) and one summary line: the newest of its status line, its last message and its newest thought.
- The page is for a portrait screen of about 1080 × 1920 CSS pixels.

### server.py and `__main__.py`

```
python3 -m agentmap --project PATH [--port 7788] [--t3-db PATH] [--hours 3] [--light-from 7] [--dark-from 19]
```

- The server binds `127.0.0.1` only.
- Routes: `GET /` (the page), `GET /stream` (Server-Sent Events), `GET /static/datastar.js`, `POST /mcp`. `GET /mcp` answers 405, because the server sends no server-to-client messages.
- `POST /mcp` with an `Origin` header that is not `http://127.0.0.1:<port>` or `http://localhost:<port>` gets 403. This blocks DNS rebinding.
- The poller thread runs every 250 ms. It renders again on three events:
  - `T3Reader.changed()` is true.
  - The status store changed.
  - 15 seconds passed since the last render. This render keeps ages such as `12m` current.
- An SSE message is:

```
event: datastar-patch-elements
data: elements <main id="app">
data: elements ...
```

  Each line of the HTML gets its own `data: elements ` prefix. The message ends with an empty line.
- A stream with no news for 15 seconds gets a comment line `: ping`.

### tickets.py and markdown.py: points that ticket-bar.md leaves open

`docs/plans/ticket-bar.md` gives the interface. These points were decided when the modules were built (2026-11-14):

- Hand-edited ticket files hold fenced blocks with a whole older ticket in them, with its own `## Notes` and timestamp lines. So the notes start at the first `## Notes` line outside a fenced block, and only a `**<ISO time>**` line outside a fenced block starts a note. A bold line that is not a time, such as `**2026-11-12, the build**`, stays in the note's text.
- Text in the notes section before the first timestamp is not a note. It stays at the end of the body, under its `## Notes` heading. Notes are sorted by time, oldest first.
- Defaults: a missing `status` is `open`, a missing `type` is `task`, and a ticket without a `# title` line takes its id as title. A priority outside 0 to 4 is 2. A list value without brackets is one item.
- "No epic" holds every ticket whose parent chain ends at itself and that has no descendants. This includes a ticket whose parent is missing. Its `closed` is the number of closed tickets of that kind.
- Order ties: a ticket or root without `created` comes after the dated ones. The last key is the id. When two files of an id have the same `changed_at`, the earlier folder of the list wins, then the first file name.
- `TicketStore` reads only `*.md` regular files. It keys each file by its modification time and its size. A file that it cannot read is left out of the cache, so the next rescan tries it again. Each worktree holds a copy of the root's tickets, so the store parses each distinct file text once (an LRU cache of 4096 texts). On the real folder set (58 folders, 34,747 files, 1,051 distinct texts), the first poll takes about 1.5 s, and a warm poll about 7 ms.
- A link opens in a new tab (`target="_blank" rel="noopener noreferrer"`), so that a click does not leave the dashboard. A link with another scheme shows only its text.

## Setup in the agent harnesses

Claude Code, in the watched project's `.mcp.json`:

```json
{
  "mcpServers": {
    "agentmap": {
      "type": "http",
      "url": "http://127.0.0.1:7788/mcp",
      "headers": { "X-T3-Thread": "${T3CODE_THREAD_ID}" }
    }
  }
}
```

Codex, in `~/.codex/config.toml`. Codex needs no header, because `caller.py` finds the calling thread:

```toml
[mcp_servers.agentmap]
url = "http://127.0.0.1:7788/mcp"
```

## Order of the work

1. Scaffold: flake, package layout, vendored Datastar, this doc.
2. `plan.py`, then `mcp.py`.
3. `t3.py`. It does not depend on step 2.
4. `treemap.py`. It does not depend on steps 2 or 3.
5. `render.py`, after steps 2 to 4.
6. `server.py` and `__main__.py`, after step 5.
7. README with the setup steps.

Each module ticket includes the full test process: tests first, then coverage, then mutation testing with mutmut.

## Light and dark themes

User request of 2026-11-14. The board has a light theme and a dark theme, and the local clock picks one.

- The board is light from the hour `--light-from` (default 7) up to the hour `--dark-from` (default 19), and dark the rest of the day. `page.is_dark` holds the rule.
- The page applies the rule to the browser's local clock with the signal `$now`, which ticks every 10 seconds. The server applies it to its own clock for the first paint, so the page does not show the light theme for a moment at night. The server and the browser run on the same Mac.
- Every color is a CSS variable in one of two blocks: `:root` for light and `:root:has(body.dark)` for dark. A test makes sure that no other rule holds a color.
- The dark theme is a warm dusk gray (`#25282c`), not black, with warm off-white ink. Text keeps a contrast of 4.5:1 or more. The two marks that are faint on purpose, the ink of facts older than an hour and the floor of the fade, keep about 4:1, which is more than in the light theme.
- Agent colors keep their hues in both themes. Each theme sets only the lightness and chroma of tags, routes, tints and accents, as `oklch(var(--h-...) var(--hue))`.
