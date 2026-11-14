"""The MCP endpoint: the smallest part of MCP Streamable HTTP that agents need.

`handle` takes one POST body and gives one HTTP answer. The server has no
server-to-client messages and no sessions, so every answer is plain JSON.
"""

from __future__ import annotations

import json
from typing import Callable, Mapping

from agentmap.caller import CallerError
from agentmap.plan import GOAL_MAX, ITEM_MAX, STATUS_MAX, Plan, PlanError, PlanStore

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_VERSION = SUPPORTED_VERSIONS[0]
THREAD_HEADER = "X-T3-Thread"
UNEXPANDED_THREAD = "${T3CODE_THREAD_ID}"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

_JSON = {"Content-Type": "application/json"}

INSTRUCTIONS = """\
agentmap shows your plan on a live dashboard next to the other agent threads \
of this project. Use it for every task with more than a few steps, both as an \
orchestrator and as a worker:
1. At the start, call set_goal with the overall goal in one or two sentences.
2. Then call add_todos with a two-level outline: the main steps, each with its \
sub-steps as children. Add steps later with add_todos; give parent to add \
sub-steps to one main step.
3. Call mark_todo with state "doing" when you start an item and "done" when you \
finish it. A step with sub-steps follows them, so mark the sub-steps.
4. After each major step, call set_status with one short line about what you \
do or wait for now.
5. After a context compaction, call get_plan to see your goal and where you are.
Each tool answers with your plan outline and its item ids."""

_NO_THREAD = f"""\
agentmap does not know which thread calls it: the {THREAD_HEADER} header is \
missing, empty or not expanded. Set it to the T3 Code thread id. For Claude \
Code, in .mcp.json, give the agentmap server \
"headers": {{"{THREAD_HEADER}": "{UNEXPANDED_THREAD}"}}. \
T3 Code sets T3CODE_THREAD_ID only in the agents that it starts."""

_OTHER_PROJECT = """\
This thread belongs to another T3 Code project. This agentmap server shows one \
project only, so it does not store the plans of other projects."""

_NO_PLAN = "No plan yet for this thread. Start one with set_goal, then add_todos."

_STATE_MARKS = {"todo": " ", "doing": "~", "done": "x"}


def _string(max_length: int, description: str) -> dict:
    return {"type": "string", "maxLength": max_length, "description": description}


TOOLS = [
    {
        "name": "set_goal",
        "description": "Set the overall goal of your task, in one or two sentences. "
                       "Call it at the start of a task with several steps.",
        "inputSchema": {"type": "object",
                        "properties": {"goal": _string(GOAL_MAX, "The overall goal.")},
                        "required": ["goal"]},
    },
    {
        "name": "set_status",
        "description": "Set one short line about what you do or wait for now. "
                       "Call it after each major step.",
        "inputSchema": {"type": "object",
                        "properties": {"status": _string(STATUS_MAX, "The current step.")},
                        "required": ["status"]},
    },
    {
        "name": "add_todos",
        "description": "Add main steps, each with optional sub-steps as children. With "
                       "parent, add sub-steps to one main step instead; then the items "
                       "have no children. Items are never deleted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "description": "The new items, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": _string(ITEM_MAX, "What the step does."),
                            "children": {"type": "array", "items": {"type": "string"},
                                         "description": "Texts of the sub-steps."},
                        },
                        "required": ["text"],
                    },
                },
                "parent": {"type": "string",
                           "description": "The id of a main step, such as \"2\"."},
            },
            "required": ["items"],
        },
    },
    {
        "name": "mark_todo",
        "description": "Mark an item without sub-steps as doing or done. "
                       "A state never moves back.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The item id, such as \"2\" or \"2.1\"."},
                "state": {"type": "string", "enum": ["doing", "done"],
                          "description": "\"doing\" when you start, \"done\" when you finish."},
            },
            "required": ["id", "state"],
        },
    },
    {
        "name": "get_plan",
        "description": "Get your plan outline with item ids, for example after a "
                       "context compaction.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def handle(body: bytes, headers: Mapping[str, str], store: PlanStore,
           thread_project: Callable[[str], bool | None],
           find_caller: Callable[[str, dict], str] | None = None,
           ) -> tuple[int, dict[str, str], bytes]:
    """Answer one POST to /mcp with (HTTP status, response headers, response body).

    The calling thread comes from the X-T3-Thread header. Without the header,
    `find_caller(tool, arguments)` names it, or raises CallerError.
    """
    try:
        message = json.loads(body.decode("utf-8"))
    except ValueError:
        return _bad_request(PARSE_ERROR, "Parse error: the body is not JSON.")
    if isinstance(message, list):
        return _bad_request(INVALID_REQUEST, "Batches are not supported. "
                                             "Send one message per request.")
    if not isinstance(message, dict):
        return _bad_request(INVALID_REQUEST, "The message must be a JSON object.")
    if "method" not in message:
        if "id" in message and ("result" in message or "error" in message):
            return 202, {}, b""  # A response from the client: nothing to answer.
        return _bad_request(INVALID_REQUEST, "The message has no method.")
    method = message["method"]
    if not isinstance(method, str):
        return _bad_request(INVALID_REQUEST, "The method must be a string.")
    if "id" not in message:
        return 202, {}, b""  # A notification: no answer.
    params = message.get("params")
    try:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise _RpcError(INVALID_PARAMS, "The params must be a JSON object.")
        result = _dispatch(method, params, headers, store, thread_project, find_caller)
    except _RpcError as error:
        return _answer(200, {"jsonrpc": "2.0", "id": message["id"],
                             "error": {"code": error.code, "message": str(error)}})
    return _answer(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})


def outline(plan: Plan | None) -> str:
    """The plan as text: goal, status line, then one line per item with its id."""
    if plan is None:
        return _NO_PLAN
    lines = []
    if plan.goal is not None:
        lines.append(f"Goal: {plan.goal}")
    if plan.status is not None:
        lines.append(f"Status: {plan.status}")
    for item in plan.items:
        lines.append(f"{item.id} [{_STATE_MARKS[item.state]}] {item.text}")
        lines.extend(f"  {kid.id} [{_STATE_MARKS[kid.state]}] {kid.text}"
                     for kid in item.children)
    return "\n".join(lines)


def _answer(status: int, payload: dict) -> tuple[int, dict[str, str], bytes]:
    return status, dict(_JSON), json.dumps(payload).encode("utf-8")


def _bad_request(code: int, message: str) -> tuple[int, dict[str, str], bytes]:
    return _answer(400, {"jsonrpc": "2.0", "id": None,
                         "error": {"code": code, "message": message}})


def _dispatch(method: str, params: dict, headers: Mapping[str, str], store: PlanStore,
              thread_project: Callable[[str], bool | None],
              find_caller: Callable[[str, dict], str] | None) -> dict:
    if method == "initialize":
        version = params.get("protocolVersion")
        return {
            "protocolVersion": version if version in SUPPORTED_VERSIONS else LATEST_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agentmap", "version": "0.1.0"},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _call_tool(params, headers, store, thread_project, find_caller)
    raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _call_tool(params: dict, headers: Mapping[str, str], store: PlanStore,
               thread_project: Callable[[str], bool | None],
               find_caller: Callable[[str, dict], str] | None) -> dict:
    name = params.get("name")
    if not isinstance(name, str) or name not in _TOOL_RUNNERS:
        raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name!r}")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    thread = _thread(headers)
    if thread is None and find_caller is not None:
        try:
            thread = find_caller(name, arguments)
        except CallerError as error:
            return _tool_result(str(error), is_error=True)
    if thread is None:
        return _tool_result(_NO_THREAD, is_error=True)
    if thread_project(thread) is False:
        return _tool_result(_OTHER_PROJECT, is_error=True)
    try:
        if not isinstance(arguments, dict):
            raise PlanError("The arguments must be a JSON object.")
        _TOOL_RUNNERS[name](store, thread, arguments)
    except PlanError as error:
        return _tool_result(str(error), is_error=True)
    return _tool_result(outline(store.plan(thread)), is_error=False)


def _tool_result(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _thread(headers: Mapping[str, str]) -> str | None:
    """The calling thread's id from its header, or None for a bad header."""
    wanted = THREAD_HEADER.lower()
    value = next((v for k, v in headers.items() if k.lower() == wanted), None)
    value = (value or "").strip()
    if not value or value == UNEXPANDED_THREAD:
        return None
    return value


def _text_argument(arguments: dict, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise PlanError(f"The argument \"{name}\" must be a string.")
    return value


def _add_todos(store: PlanStore, thread: str, arguments: dict) -> None:
    items = arguments.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PlanError("The argument \"items\" must be a list of objects such as "
                        "{\"text\": \"...\", \"children\": [\"...\"]}.")
    children = None
    if any("children" in item for item in items):
        children = [item.get("children", []) for item in items]
        if not all(isinstance(group, list) for group in children):
            raise PlanError("The \"children\" of an item must be a list of strings.")
    parent = arguments.get("parent")
    if parent is not None and not isinstance(parent, str):
        raise PlanError("The argument \"parent\" must be a string id such as \"2\".")
    store.add_items(thread, [item.get("text") for item in items], parent=parent,
                    children=children)


_TOOL_RUNNERS: dict[str, Callable[[PlanStore, str, dict], None]] = {
    "set_goal": lambda store, thread, args: store.set_goal(
        thread, _text_argument(args, "goal")),
    "set_status": lambda store, thread, args: store.set_status(
        thread, _text_argument(args, "status")),
    "add_todos": _add_todos,
    "mark_todo": lambda store, thread, args: store.mark(
        thread, _text_argument(args, "id"), _text_argument(args, "state")),
    "get_plan": lambda store, thread, args: None,
}
