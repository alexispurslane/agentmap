"""The MCP endpoint: the smallest part of MCP Streamable HTTP that agents need.

`handle` takes one POST body and gives one HTTP answer. The server has no
server-to-client messages and no sessions, so every answer is plain JSON.
"""

from __future__ import annotations

import json
from typing import Callable, Mapping

from agentmap.caller import CallerError
from agentmap.status import STATUS_MAX, StatusError, StatusStore

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
agentmap shows every agent thread of this project on a live dashboard. After \
each major step, call set_status with one short line about what you do or wait \
for now. If you work on a tk ticket, start the line with its ticket id, for \
example "ter-69s8: measuring the tile cap", so that the dashboard links you to \
the ticket."""

_NO_THREAD = f"""\
agentmap does not know which thread calls it: the {THREAD_HEADER} header is \
missing, empty or not expanded. Set it to the T3 Code thread id. For Claude \
Code, in .mcp.json, give the agentmap server \
"headers": {{"{THREAD_HEADER}": "{UNEXPANDED_THREAD}"}}. \
T3 Code sets T3CODE_THREAD_ID only in the agents that it starts."""

_OTHER_PROJECT = """\
This thread belongs to another T3 Code project. This agentmap server shows one \
project only, so it does not store the statuses of other projects."""


def _string(max_length: int, description: str) -> dict:
    return {"type": "string", "maxLength": max_length, "description": description}


TOOLS = [
    {
        "name": "set_status",
        "description": "Post one short line about what you do or wait for now. It "
                       "replaces your previous status line.",
        "inputSchema": {
            "type": "object",
            "properties": {"status": _string(
                STATUS_MAX, "What you do or wait for now, in one line. Start with "
                            "the tk ticket id if you work on a ticket.")},
            "required": ["status"],
        },
    },
]


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def handle(body: bytes, headers: Mapping[str, str], store: StatusStore,
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




def _answer(status: int, payload: dict) -> tuple[int, dict[str, str], bytes]:
    return status, dict(_JSON), json.dumps(payload).encode("utf-8")


def _bad_request(code: int, message: str) -> tuple[int, dict[str, str], bytes]:
    return _answer(400, {"jsonrpc": "2.0", "id": None,
                         "error": {"code": code, "message": message}})


def _dispatch(method: str, params: dict, headers: Mapping[str, str], store: StatusStore,
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


def _call_tool(params: dict, headers: Mapping[str, str], store: StatusStore,
               thread_project: Callable[[str], bool | None],
               find_caller: Callable[[str, dict], str] | None) -> dict:
    name = params.get("name")
    if name != "set_status":
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
            raise StatusError("The arguments must be a JSON object.")
        store.set_status(thread, _text_argument(arguments, "status"))
    except StatusError as error:
        return _tool_result(str(error), is_error=True)
    return _tool_result(f"Status posted: {store.status(thread).text}", is_error=False)


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
        raise StatusError(f"The argument \"{name}\" must be a string.")
    return value
