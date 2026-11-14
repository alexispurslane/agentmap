"""Behavior of the MCP endpoint: JSON-RPC over HTTP POST, five plan tools."""

import email.message
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from agentmap import mcp
from agentmap.plan import PlanStore

THREAD = "deb79e0c-thread"


class EndpointTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PlanStore(pathlib.Path(self._tmp.name) / "plan.jsonl")
        self.project_answer = True
        self.asked = []

    def thread_project(self, thread):
        self.asked.append(thread)
        return self.project_answer

    def post(self, message, headers=None):
        """POST one message. `message` is bytes, or an object to encode as JSON."""
        body = message if isinstance(message, bytes) else json.dumps(message).encode()
        if headers is None:
            headers = {"X-T3-Thread": THREAD}
        return mcp.handle(body, headers, self.store, self.thread_project)

    def rpc(self, method, params=None, id=1, headers=None):
        """Send one request and return the decoded response body."""
        message = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            message["params"] = params
        status, response_headers, body = self.post(message, headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers, {"Content-Type": "application/json"})
        return json.loads(body)

    def call(self, tool, arguments=None, headers=None):
        """Call one tool. Return (is_error, text)."""
        params = {"name": tool}
        if arguments is not None:
            params["arguments"] = arguments
        result = self.rpc("tools/call", params, headers=headers)["result"]
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        return result["isError"], result["content"][0]["text"]

    def assertBadRequest(self, message, code):
        status, headers, body = self.post(message)
        self.assertEqual(status, 400)
        self.assertEqual(headers, {"Content-Type": "application/json"})
        answer = json.loads(body)
        self.assertEqual(answer, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": code, "message": mock.ANY}})
        self.assertIsInstance(answer["error"]["message"], str)
        self.assertTrue(answer["error"]["message"])


class Initialize(EndpointTestCase):
    def test_the_server_answers_with_the_clients_version_when_it_supports_it(self):
        for version in ["2025-06-18", "2025-03-26", "2024-11-05"]:
            result = self.rpc("initialize", {"protocolVersion": version})["result"]
            self.assertEqual(result["protocolVersion"], version)

    def test_the_server_answers_with_2025_06_18_for_another_version(self):
        for params in [{"protocolVersion": "2099-01-01"}, {"protocolVersion": 7}, {}, None]:
            result = self.rpc("initialize", params)["result"]
            self.assertEqual(result["protocolVersion"], "2025-06-18")

    def test_the_server_offers_tools_and_names_itself(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"]["name"], "agentmap")
        self.assertIsInstance(result["serverInfo"]["version"], str)

    def test_the_instructions_tell_the_moment_for_each_tool(self):
        text = self.rpc("initialize", {})["result"]["instructions"]
        for tool in ["set_goal", "add_todos", "mark_todo", "set_status", "get_plan"]:
            self.assertIn(tool, text)
        self.assertIn("compact", text)
        self.assertLess(len(text), 1500)

    def test_the_server_version_is_the_package_version(self):
        import tomllib
        pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        version = tomllib.loads(pyproject.read_text())["project"]["version"]
        self.assertEqual(self.rpc("initialize", {})["result"]["serverInfo"]["version"], version)

    def test_initialize_needs_no_thread_header(self):
        result = self.rpc("initialize", {}, headers={})["result"]
        self.assertIn("instructions", result)


class Messages(EndpointTestCase):
    def test_a_response_carries_the_request_id(self):
        self.assertEqual(self.rpc("ping", id=41)["id"], 41)
        self.assertEqual(self.rpc("ping", id="abc")["id"], "abc")
        self.assertEqual(self.rpc("ping")["jsonrpc"], "2.0")

    def test_ping_gets_an_empty_result(self):
        self.assertEqual(self.rpc("ping"), {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_a_notification_gets_202_and_an_empty_body(self):
        answer = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(answer, (202, {}, b""))

    def test_a_notification_of_any_method_runs_nothing(self):
        answer = self.post({"jsonrpc": "2.0", "method": "tools/call",
                            "params": {"name": "set_goal", "arguments": {"goal": "x"}}})
        self.assertEqual(answer, (202, {}, b""))
        self.assertIsNone(self.store.plan(THREAD))

    def test_a_response_from_the_client_gets_202_and_an_empty_body(self):
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 5, "result": {}}), (202, {}, b""))
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 5, "error": {"code": 1}}),
                         (202, {}, b""))

    def test_an_unknown_method_gets_error_32601(self):
        answer = self.rpc("resources/list", id=3)
        self.assertEqual(answer, {"jsonrpc": "2.0", "id": 3,
                                  "error": {"code": -32601, "message": mock.ANY}})
        self.assertIn("resources/list", answer["error"]["message"])

    def test_bad_json_gets_error_32700(self):
        self.assertBadRequest(b"{nope", -32700)
        self.assertBadRequest(b"\xff\xfe", -32700)
        self.assertBadRequest(b"", -32700)

    def test_a_batch_gets_error_32600(self):
        self.assertBadRequest([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], -32600)

    def test_a_message_that_is_not_a_request_gets_error_32600(self):
        self.assertBadRequest("ping", -32600)
        self.assertBadRequest({"jsonrpc": "2.0", "id": 1}, -32600)
        self.assertBadRequest({"jsonrpc": "2.0", "id": 1, "method": 5}, -32600)

    def test_params_that_are_not_an_object_get_error_32602(self):
        answer = self.rpc("tools/call", ["set_goal"])
        self.assertEqual(answer["error"]["code"], -32602)
        self.assertIn("params", answer["error"]["message"])


class ToolList(EndpointTestCase):
    def tools(self):
        return {tool["name"]: tool for tool in self.rpc("tools/list")["result"]["tools"]}

    def test_the_server_lists_five_tools(self):
        self.assertEqual(list(self.tools()),
                         ["set_goal", "set_status", "add_todos", "mark_todo", "get_plan"])

    def test_each_tool_has_a_description_and_an_object_schema(self):
        for name, tool in self.tools().items():
            self.assertTrue(tool["description"], name)
            self.assertEqual(tool["inputSchema"]["type"], "object", name)

    def test_each_input_has_a_json_type_and_a_description(self):
        for name, tool in self.tools().items():
            for key, schema in tool["inputSchema"]["properties"].items():
                self.assertIn(schema["type"], ["string", "array"], (name, key))
                self.assertTrue(schema["description"], (name, key))
        item = self.tools()["add_todos"]["inputSchema"]["properties"]["items"]["items"]
        self.assertEqual(item["properties"]["text"]["type"], "string")
        self.assertEqual(item["properties"]["text"]["maxLength"], 200)
        self.assertTrue(item["properties"]["text"]["description"])

    def test_the_schemas_require_the_documented_inputs(self):
        tools = self.tools()
        required = {name: tool["inputSchema"].get("required", []) for name, tool in tools.items()}
        self.assertEqual(required, {"set_goal": ["goal"], "set_status": ["status"],
                                    "add_todos": ["items"], "mark_todo": ["id", "state"],
                                    "get_plan": []})
        self.assertEqual(tools["mark_todo"]["inputSchema"]["properties"]["state"]["enum"],
                         ["doing", "done"])
        item = tools["add_todos"]["inputSchema"]["properties"]["items"]["items"]
        self.assertEqual(item["required"], ["text"])
        self.assertEqual(item["properties"]["children"]["items"], {"type": "string"})
        self.assertEqual(tools["add_todos"]["inputSchema"]["properties"]["parent"]["type"],
                         "string")

    def test_the_schemas_state_the_text_limits(self):
        tools = self.tools()
        self.assertEqual(tools["set_goal"]["inputSchema"]["properties"]["goal"]["maxLength"], 500)
        self.assertEqual(
            tools["set_status"]["inputSchema"]["properties"]["status"]["maxLength"], 160)


class ThreadHeader(EndpointTestCase):
    def test_the_tool_acts_on_the_thread_named_in_the_header(self):
        self.call("set_goal", {"goal": "Goal"}, headers={"X-T3-Thread": "other-thread"})
        self.assertEqual(self.store.plan("other-thread").goal, "Goal")
        self.assertIsNone(self.store.plan(THREAD))

    def test_the_header_name_is_case_insensitive(self):
        self.call("set_goal", {"goal": "Goal"}, headers={"x-t3-thread": THREAD})
        self.assertEqual(self.store.plan(THREAD).goal, "Goal")

    def test_the_header_is_read_from_an_http_message(self):
        message = email.message.Message()
        message["X-T3-THREAD"] = THREAD
        self.call("set_goal", {"goal": "Goal"}, headers=message)
        self.assertEqual(self.store.plan(THREAD).goal, "Goal")

    def test_a_missing_empty_or_unexpanded_header_is_a_tool_error_that_tells_how_to_set_it(self):
        for headers in [{}, {"X-T3-Thread": ""}, {"X-T3-Thread": "  "},
                        {"X-T3-Thread": "${T3CODE_THREAD_ID}"}]:
            is_error, text = self.call("set_goal", {"goal": "Goal"}, headers=headers)
            self.assertTrue(is_error, headers)
            self.assertIn("X-T3-Thread", text)
            self.assertIn("T3CODE_THREAD_ID", text)
        self.assertEqual(self.store.plans(), {})
        self.assertEqual(self.asked, [])

    def test_a_thread_of_another_project_gets_a_tool_error(self):
        self.project_answer = False
        is_error, text = self.call("set_goal", {"goal": "Goal"})
        self.assertTrue(is_error)
        self.assertIn("another", text)
        self.assertEqual(self.asked, [THREAD])
        self.assertEqual(self.store.plans(), {})

    def test_a_thread_that_t3_code_does_not_know_yet_may_post(self):
        self.project_answer = None
        is_error, _ = self.call("set_goal", {"goal": "Goal"})
        self.assertFalse(is_error)
        self.assertEqual(self.store.plan(THREAD).goal, "Goal")


class Tools(EndpointTestCase):
    def test_set_goal_sets_the_goal_and_returns_the_outline(self):
        self.assertEqual(self.call("set_goal", {"goal": " Ship it "}), (False, "Goal: Ship it"))

    def test_set_status_sets_the_status_line(self):
        self.call("set_goal", {"goal": "Ship it"})
        self.assertEqual(self.call("set_status", {"status": "Reading"}),
                         (False, "Goal: Ship it\nStatus: Reading"))

    def test_add_todos_adds_top_level_items_with_children(self):
        is_error, text = self.call("add_todos", {"items": [
            {"text": "Read", "children": ["Read a", "Read b"]},
            {"text": "Write"},
        ]})
        self.assertFalse(is_error)
        self.assertEqual(text, "1 [ ] Read\n  1.1 [ ] Read a\n  1.2 [ ] Read b\n2 [ ] Write")

    def test_add_todos_with_a_parent_adds_children_to_that_item(self):
        self.call("add_todos", {"items": [{"text": "Read"}]})
        is_error, text = self.call("add_todos", {"parent": "1", "items": [{"text": "Read a"}]})
        self.assertFalse(is_error)
        self.assertEqual(text, "1 [ ] Read\n  1.1 [ ] Read a")

    def test_add_todos_with_a_parent_rejects_items_with_children(self):
        self.call("add_todos", {"items": [{"text": "Read"}]})
        is_error, text = self.call("add_todos", {"parent": "1", "items": [
            {"text": "Read a", "children": ["deeper"]}]})
        self.assertTrue(is_error)
        self.assertIn("two levels", text)
        self.assertEqual(self.store.plan(THREAD).items[0].children, ())

    def test_mark_todo_moves_an_item_forward(self):
        self.call("add_todos", {"items": [{"text": "Read"}]})
        self.assertEqual(self.call("mark_todo", {"id": "1", "state": "doing"}),
                         (False, "1 [~] Read"))
        self.assertEqual(self.call("mark_todo", {"id": "1", "state": "done"}),
                         (False, "1 [x] Read"))

    def test_a_rejected_request_is_a_tool_error_with_the_store_message(self):
        is_error, text = self.call("mark_todo", {"id": "4", "state": "done"})
        self.assertTrue(is_error)
        self.assertIn("no item 4", text)

    def test_get_plan_returns_the_outline_in_the_documented_format(self):
        self.call("set_goal", {"goal": "Cap lights per tile so the deck holds 60 fps."})
        self.call("set_status", {"status": "Waiting on the timed take."})
        self.call("add_todos", {"items": [
            {"text": "Diagnose budget overrun", "children": ["Count lights per tile"]},
            {"text": "Prototype per-tile cap", "children": [
                "Cap in cull pass", "Timed take on hall and deck", "Mutation pass"]},
            {"text": "Merge and close ticket"},
        ]})
        self.call("mark_todo", {"id": "1.1", "state": "done"})
        self.call("mark_todo", {"id": "2.1", "state": "done"})
        self.call("mark_todo", {"id": "2.2", "state": "doing"})
        self.assertEqual(self.call("get_plan"), (False, (
            "Goal: Cap lights per tile so the deck holds 60 fps.\n"
            "Status: Waiting on the timed take.\n"
            "1 [x] Diagnose budget overrun\n"
            "  1.1 [x] Count lights per tile\n"
            "2 [~] Prototype per-tile cap\n"
            "  2.1 [x] Cap in cull pass\n"
            "  2.2 [~] Timed take on hall and deck\n"
            "  2.3 [ ] Mutation pass\n"
            "3 [ ] Merge and close ticket"
        )))

    def test_get_plan_without_a_plan_says_how_to_start_one(self):
        is_error, text = self.call("get_plan", {})
        self.assertFalse(is_error)
        self.assertIn("No plan", text)
        self.assertIn("set_goal", text)

    def test_get_plan_shows_only_this_threads_plan(self):
        self.call("set_goal", {"goal": "Mine"})
        self.call("set_goal", {"goal": "Theirs"}, headers={"X-T3-Thread": "other"})
        self.assertEqual(self.call("get_plan"), (False, "Goal: Mine"))

    def test_an_unknown_tool_gets_error_32602(self):
        answer = self.rpc("tools/call", {"name": "delete_plan", "arguments": {}})
        self.assertEqual(answer["error"]["code"], -32602)
        self.assertIn("delete_plan", answer["error"]["message"])

    def test_arguments_of_the_wrong_shape_are_a_tool_error_that_names_the_argument(self):
        cases = [
            ("set_goal", {}, "goal"),
            ("set_goal", {"goal": 5}, "goal"),
            ("set_goal", None, "goal"),
            ("set_goal", "Ship it", "arguments"),
            ("set_status", {"text": "x"}, "status"),
            ("add_todos", {}, "items"),
            ("add_todos", {"items": "Read"}, "items"),
            ("add_todos", {"items": ["Read"]}, "items"),
            ("add_todos", {"items": [{"title": "Read"}]}, "text"),
            ("add_todos", {"items": [{"text": "Read", "children": "a"}]}, "children"),
            ("add_todos", {"items": [{"text": "Read"}], "parent": 1}, "parent"),
            ("mark_todo", {"id": "1"}, "state"),
            ("mark_todo", {"state": "done"}, "id"),
        ]
        for tool, arguments, name in cases:
            is_error, text = self.call(tool, arguments)
            self.assertTrue(is_error, (tool, arguments))
            self.assertIn(name, text, (tool, arguments))
        self.assertEqual(self.store.plans(), {})

    def test_a_tool_call_without_a_name_gets_error_32602(self):
        answer = self.rpc("tools/call", {"arguments": {}})
        self.assertEqual(answer["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
