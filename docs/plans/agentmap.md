# agentmap: a live dashboard of agent threads

agentmap is a dashboard for a portrait side monitor. It shows what every Claude Code and Codex thread in one T3 Code project does now. It shows each thread in two ways:

- In space: a dot on a map of the project, in the area of the files that the thread works on.
- In the ownership tree: a card under the card of the thread that started it.

Agents can also post their own overall goal and a two-level todo list through a small MCP server in the same process.

## Decisions

The user chose these points on 2026-11-14:

| Point | Decision |
|---|---|
| Where space shows | One shared map over the top part of the screen, with one dot per thread. |
| Map shape | A treemap: each directory gets an area in proportion to its code size. |
| Where the plan log is stored | In the watched project's repository, in `.agentmap/plan.jsonl`. |
| How a Codex caller is identified | By the `X-T3-Thread` header if it is there, else by T3 Code's record of the call (see `caller.py`). |

Other fixed points:

- The backend uses the Python 3.14 standard library only. Test tools (coverage, pytest, mutmut) come from the Nix dev shell.
- The frontend is Datastar 1.0.5, vendored in `agentmap/static/datastar.js`. The server sends HTML fragments. The page has no other script.
- One server process watches one project. It serves the page, the event stream and the MCP endpoint on one port.

## Terms

- Thread: one T3 Code thread. A subagent started by `delegate_task` or by Claude's Agent tool is also a thread, with a parent.
- Root thread: a thread with no parent.
- Plan: the goal, the status line and the todo items that one thread posts through MCP.
- Item: one entry in a plan's todo list. A top-level item can have child items. A child item cannot have children.
- Now line: one line of text that tells what the thread does at this moment, for example `Editing src/render/cull.rs`.
- Cell: one rectangle of the treemap. A cell stands for one directory, or for the files directly in one directory.
- Touched path: a file path, relative to the project root, that a thread read or changed.

## Data flow

```
T3 Code statev2.sqlite --(read-only, poll)--> t3.py  --\
project files (git ls-files) --> treemap.py ------------>  render.py --> SSE /stream --> Datastar page
.agentmap/plan.jsonl <--> plan.py <-- mcp.py <-- POST /mcp <-- agents
```

The poller thread in `server.py` is the only caller of `t3.py` and `render.py`. It renders the whole `<main id="app">` element. If the HTML differs from the last HTML, it publishes it to every open stream. Datastar morphs the new element into the page, so the DOM changes only where the HTML changed.

## Module contracts

All modules live in the package `agentmap/`. Tests live in `tests/` and use `unittest`. Each module below has one owner ticket.

### plan.py: the plan store

The plan store keeps the plans of all threads of one project. Its source of truth is an append-only log, one JSON object per line. The in-memory state is the fold of the log.

```python
class PlanError(ValueError): ...           # a rejected request; the message is shown to the agent

class PlanStore:
    def __init__(self, log_path: pathlib.Path): ...   # replays the log if it exists
    def set_goal(self, thread: str, text: str) -> None: ...
    def set_status(self, thread: str, text: str) -> None: ...
    def add_items(self, thread: str, texts: list[str], parent: str | None = None,
                  children: list[list[str]] | None = None) -> list[str]: ...  # returns new ids
    def mark(self, thread: str, item_id: str, state: str) -> None: ...  # state is "doing" or "done"
    def plan(self, thread: str) -> Plan | None: ...
    def plans(self) -> dict[str, Plan]: ...
    def check_invariants(self) -> None: ...   # raises AssertionError on a broken invariant
```

`Plan` and `Item` are frozen dataclasses: `Plan(goal, status, items, updated_at)` and `Item(id, text, state, children)`.

Rules:

- Item ids are positions: top-level items are `"1"`, `"2"`, and so on. Children of item 2 are `"2.1"`, `"2.2"`, and so on. Because no item is ever deleted, an id never changes.
- A leaf item has the state `todo`, `doing` or `done`. The state moves only forward. A jump from `todo` to `done` is legal. A request to move back raises `PlanError`. A request for the current state does nothing.
- An item with children has a derived state, with three cases:
  - When all children are done, the state is `done`.
  - When at least one child is `doing` or `done`, but not all children are done, the state is `doing`.
  - When all children are `todo`, the state is `todo`.
- A request to mark an item with children raises `PlanError`.
- A done item gets no new children. Thus a done item stays done. The request raises `PlanError` with a hint to add a new top-level item.
- Texts are stripped. An empty text raises `PlanError`. A goal has at most 500 characters, a status line 160, an item 200. Longer text raises `PlanError`.
- Each change appends one line to the log before it changes memory, and the store flushes the file. A lock makes the methods safe across threads.
- A log line that does not parse is skipped with a warning on stderr. The store does not stop for it.

Log lines:

```json
{"ts": "2026-11-14T14:40:00Z", "thread": "deb79e0c-...", "op": "goal", "text": "..."}
{"ts": "...", "thread": "...", "op": "status", "text": "..."}
{"ts": "...", "thread": "...", "op": "add", "parent": null, "texts": ["a", "b"], "children": [["a1"], []]}
{"ts": "...", "thread": "...", "op": "mark", "id": "2.1", "state": "done"}
```

Invariants, which `check_invariants` tests:

1. In each plan, top-level ids are `1..n` without gaps, and the children of item `k` are `k.1..k.m` without gaps.
2. No item is deeper than two levels.
3. Each leaf state is one of `todo`, `doing`, `done`.
4. Each parent state equals the state derived from its children.
5. A fresh replay of the log gives the same plans as the memory.

### mcp.py: the MCP endpoint

`mcp.py` implements the smallest part of the MCP Streamable HTTP transport that Claude Code and Codex need. It has no network code. The server calls one function:

```python
def handle(body: bytes, headers: Mapping[str, str], store: PlanStore,
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
  - The `instructions` text tells the agent the right moment for each tool.
- `ping`: an empty result.
- `tools/list`: the five tools below with JSON Schema inputs.
- `tools/call`: runs the tool. A `PlanError` becomes a result with `isError: true` and the message as text. A success returns the plan outline as text.
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
| `set_goal` | `goal: string` | Sets the overall goal of the calling thread. |
| `set_status` | `status: string` | Sets one line about the current step. |
| `add_todos` | `items: [{text, children?: [string]}]`, `parent?: string` | Adds top-level items, or with `parent`, adds children to one top-level item. With `parent`, an item must not have `children`. |
| `mark_todo` | `id: string`, `state: "doing" \| "done"` | Moves one leaf item forward. |
| `get_plan` | none | Returns the plan outline with ids. |

The outline text format:

```
Goal: Cap lights per tile so the deck holds 60 fps.
Status: Waiting on the timed take.
1 [x] Diagnose budget overrun
  1.1 [x] Count lights per tile
2 [~] Prototype per-tile cap
  2.1 [x] Cap in cull pass
  2.2 [~] Timed take on hall and deck
  2.3 [ ] Mutation pass
3 [ ] Merge and close ticket
```

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
```

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
| `now` | The now line, or `None`. |
| `thought` | The first sentence of the newest thinking text, at most 160 characters, or `None`. |
| `last_said` | The first sentence of the newest assistant message, at most 160 characters, or `None`. |
| `touched` | Up to 8 touched paths, newest first, without repeats. |

Facts about the database (schema of T3 Code on 2026-11-14):

- `projection_projects(project_id, workspace_root, deleted_at)` maps a project root to project ids. More than one id can have the same root. Use all of them.
- `orchestration_v2_projection_threads` has one row per thread. Its `payload_json` has `lineage.parentThreadId`, `worktreePath`, `modelSelection.model` and `providerInstanceId`. Skip rows with `deleted_at` or `archived_at`.
- `orchestration_v2_projection_runs` has the runs of a thread, with `ordinal` and `status`. The newest run's status gives the state: `queued`, `preparing`, `starting`, `running` and `waiting` mean working. `failed` means failed. Other values mean idle.
- `orchestration_v2_projection_turn_items` has each step of a run. The `type` column and `payload_json` hold the content. A waiting `user_input_request`, `approval_request` or `secret_request` item means `needs_you`.
- The database is in WAL mode. Open it with `file:...?mode=ro` and `uri=True`.

Which threads a snapshot holds: threads of the project with `updated_at` inside the window, plus all ancestors of those threads, so that every tree is whole.

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

Touched paths come from `file_change.fileName` and from the `file_path`, `path` or `notebook_path` input of `dynamic_tool` items. A path inside the thread's worktree or inside the project root becomes relative to that root. Another path is not a touched path.

Speed: one `snapshot` call must take under 50 ms on the real database with 50 threads in the window. Cache the result per thread. When the newest item of a thread changed, read its turn items again.

### treemap.py: the project map

```python
def code_weights(root: pathlib.Path) -> dict[str, int]: ...   # relative file path -> line count
def build_cells(weights: dict[str, int], width: float, height: float) -> list[Cell]: ...
def locate(cells: list[Cell], rel_path: str) -> Cell | None: ...
```

`Cell` is a frozen dataclass: `Cell(key, label, depth, x, y, w, h, weight)`.

- `code_weights` lists files with `git ls-files -z --cached --others --exclude-standard`. Thus a new file that an agent creates shows before anyone commits it. If the root is not a git repository, it walks the tree and skips hidden directories, `node_modules`, `target` and `build`. A file with a NUL byte in its first 8 KiB counts 0 lines.
- The map has two levels. Level 1 is each top-level directory. All files directly in the root form one cell with the key `.`. Level 2 is each subdirectory of a top-level directory, plus one cell for the files directly in it, with the key `<dir>/.`.
- Order: in each level, cells are sorted by name. The layout is an ordered strip treemap (Bederson, Shneiderman and Wattenberg, 2002). The method keeps the name order. Thus, when sizes change, a directory stays in about the same place. The method was made for ordered data that changes over time, at tens to thousands of items. Our maps have about 10 to 60 cells, so the method fits.
- A level-2 cell under 1.5 % of the map area joins one `…` cell in its parent. A level-1 cell under 1.5 % joins one `…` cell at level 1.
- `locate` returns the deepest cell whose key covers the path. A `…` cell covers the paths of the cells that it joined.
- The server builds the cells at start and again every 10 minutes.

### render.py: the HTML

```python
def render_page(app_html: str) -> Safe: ...
def render_app(threads: list[AgentThread], plans: dict[str, Plan], cells: list[Cell],
               now: datetime, project_name: str) -> Safe: ...
```

- `render_page` returns the whole document. The body has `data-init="@get('/stream', {openWhenHidden: true})"`.
- `render_app` returns one `<main id="app">` element with `<section id="map">` and `<section id="tree">`.
- Each thread gets its own hue, subagents included. In order of creation, each hue is the one before plus the golden angle (137.5°). Up to 8 threads, two hues are at least 32° apart.
- A card's list of subagents has a thin guide line in the card's own hue. The line shows ownership.
- A dot and its card are linked by the frontend signal `$hover`. Each sets it to its tag while the pointer is on it, and each gets the class `hl` while `$hover` holds its tag.
- Each thread gets a tag: the root gets a letter (`A`, `B`, ...). A child gets its parent's tag plus its position, for example `B2` and `B2.1`. The tag shows on the dot and on the card, so that the reader can match them.
- A dot sits in the cell of the thread's newest touched path. Dots in one cell sit on a small grid inside the cell, in tag order. A thread without a touched path has no dot.
- A card shows the tag, title, provider and model, state, age, now line, thought, last said, status line and plan.
- A cell with the key `.` or `<dir>/.` has the label `files`.
- A plan shows its goal, then its items. A done top-level item shows as one line with a count, for example `✓ Diagnose budget overrun (3/3)`.
- An idle child thread whose `updated_at` is more than 10 minutes old shows as one line.
- All HTML is built with t-strings (PEP 750, Python 3.14) and `markup.html()`. `html()` escapes every interpolated value. The only exception is a `Safe` value, which is HTML that `html()` already made. Do not build HTML with f-strings.
- The page is for a portrait screen of about 1080 × 1920 CSS pixels. The map takes about the top third.

### server.py and `__main__.py`

```
python3 -m agentmap --project PATH [--port 7788] [--t3-db PATH] [--hours 3]
```

- The server binds `127.0.0.1` only.
- Routes: `GET /` (the page), `GET /stream` (Server-Sent Events), `GET /static/datastar.js`, `POST /mcp`. `GET /mcp` answers 405, because the server sends no server-to-client messages.
- `POST /mcp` with an `Origin` header that is not `http://127.0.0.1:<port>` or `http://localhost:<port>` gets 403. This blocks DNS rebinding.
- The poller thread runs every 250 ms. It renders again on three events:
  - `T3Reader.changed()` is true.
  - The plan store changed.
  - 15 seconds passed since the last render. This render keeps ages such as `12m` current.
- An SSE message is:

```
event: datastar-patch-elements
data: elements <main id="app">
data: elements ...
```

  Each line of the HTML gets its own `data: elements ` prefix. The message ends with an empty line.
- A stream with no news for 15 seconds gets a comment line `: ping`.

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
