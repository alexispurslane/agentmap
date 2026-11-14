"""Behavior of the MCP endpoint: JSON-RPC over HTTP POST, one status tool."""

import email.message
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from agentmap import mcp
from agentmap.caller import CallerError
from agentmap.status import Status, StatusStore

THREAD = "deb79e0c-thread"


class EndpointTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = StatusStore(pathlib.Path(self._tmp.name) / "status.jsonl")
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

    def test_the_instructions_tell_when_to_post_a_status_and_how_to_name_a_ticket(self):
        text = self.rpc("initialize", {})["result"]["instructions"]
        self.assertIn("set_status", text)
        self.assertIn("ticket id", text)
        for old in ["set_goal", "add_todos", "mark_todo", "get_plan"]:
            self.assertNotIn(old, text)
        self.assertLess(len(text), 800)

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
                            "params": {"name": "set_status", "arguments": {"status": "x"}}})
        self.assertEqual(answer, (202, {}, b""))
        self.assertIsNone(self.store.status(THREAD))

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
        answer = self.rpc("tools/call", ["set_status"])
        self.assertEqual(answer["error"]["code"], -32602)
        self.assertIn("params", answer["error"]["message"])


class ToolList(EndpointTestCase):
    def tools(self):
        return {tool["name"]: tool for tool in self.rpc("tools/list")["result"]["tools"]}

    def test_the_server_lists_one_tool_set_status(self):
        self.assertEqual(list(self.tools()), ["set_status"])

    def test_set_status_has_a_description_and_a_schema_with_its_limit(self):
        tool = self.tools()["set_status"]
        self.assertTrue(tool["description"])
        self.assertEqual(tool["inputSchema"]["type"], "object")
        self.assertEqual(tool["inputSchema"]["required"], ["status"])
        status = tool["inputSchema"]["properties"]["status"]
        self.assertEqual((status["type"], status["maxLength"]), ("string", 160))
        self.assertTrue(status["description"])


class ThreadHeader(EndpointTestCase):
    def test_the_tool_acts_on_the_thread_named_in_the_header(self):
        self.call("set_status", {"status": "Goal"}, headers={"X-T3-Thread": "other-thread"})
        self.assertEqual(self.store.status("other-thread").text, "Goal")
        self.assertIsNone(self.store.status(THREAD))

    def test_the_header_name_is_case_insensitive(self):
        self.call("set_status", {"status": "Goal"}, headers={"x-t3-thread": THREAD})
        self.assertEqual(self.store.status(THREAD).text, "Goal")

    def test_the_header_is_read_from_an_http_message(self):
        message = email.message.Message()
        message["X-T3-THREAD"] = THREAD
        self.call("set_status", {"status": "Goal"}, headers=message)
        self.assertEqual(self.store.status(THREAD).text, "Goal")

    def test_a_missing_empty_or_unexpanded_header_is_a_tool_error_that_tells_how_to_set_it(self):
        for headers in [{}, {"X-T3-Thread": ""}, {"X-T3-Thread": "  "},
                        {"X-T3-Thread": "${T3CODE_THREAD_ID}"}]:
            is_error, text = self.call("set_status", {"status": "Goal"}, headers=headers)
            self.assertTrue(is_error, headers)
            self.assertIn("X-T3-Thread", text)
            self.assertIn("T3CODE_THREAD_ID", text)
        self.assertEqual(self.store.statuses(), {})
        self.assertEqual(self.asked, [])

    def test_a_thread_of_another_project_gets_a_tool_error(self):
        self.project_answer = False
        is_error, text = self.call("set_status", {"status": "Goal"})
        self.assertTrue(is_error)
        self.assertIn("another", text)
        self.assertEqual(self.asked, [THREAD])
        self.assertEqual(self.store.statuses(), {})

    def test_a_thread_that_t3_code_does_not_know_yet_may_post(self):
        self.project_answer = None
        is_error, _ = self.call("set_status", {"status": "Goal"})
        self.assertFalse(is_error)
        self.assertEqual(self.store.status(THREAD).text, "Goal")


class Tools(EndpointTestCase):
    def test_set_status_posts_the_status_and_echoes_it(self):
        self.assertEqual(self.call("set_status", {"status": " Reading the cull pass "}),
                         (False, "Status posted: Reading the cull pass"))
        self.assertEqual(self.store.status(THREAD).text, "Reading the cull pass")

    def test_a_refused_status_is_a_tool_error_with_the_store_message(self):
        is_error, text = self.call("set_status", {"status": "x" * 161})
        self.assertTrue(is_error)
        self.assertIn("160", text)
        self.assertEqual(self.store.statuses(), {})

    def test_the_old_plan_tools_are_unknown(self):
        for name in ("set_goal", "add_todos", "mark_todo", "get_plan"):
            answer = self.rpc("tools/call", {"name": name, "arguments": {}})
            self.assertEqual(answer["error"]["code"], -32602, name)

    def test_an_unknown_tool_gets_error_32602(self):
        answer = self.rpc("tools/call", {"name": "delete_plan", "arguments": {}})
        self.assertEqual(answer["error"]["code"], -32602)
        self.assertIn("delete_plan", answer["error"]["message"])

    def test_arguments_of_the_wrong_shape_are_a_tool_error_that_names_the_argument(self):
        cases = [
            ({}, "status"),
            ({"status": 5}, "status"),
            (None, "status"),
            ({"text": "x"}, "status"),
            ("Reading", "arguments"),
        ]
        for arguments, name in cases:
            is_error, text = self.call("set_status", arguments)
            self.assertTrue(is_error, arguments)
            self.assertIn(name, text, arguments)
        self.assertEqual(self.store.statuses(), {})

    def test_arguments_that_are_not_an_object_get_a_plain_message(self):
        self.assertEqual(self.call("set_status", "Reading"),
                         (True, "The arguments must be a JSON object."))

    def test_a_tool_call_without_a_name_gets_error_32602(self):
        answer = self.rpc("tools/call", {"arguments": {}})
        self.assertEqual(answer["error"]["code"], -32602)


class CallerWithoutHeader(EndpointTestCase):
    """Codex threads share one process, so they call without a thread header."""

    def setUp(self):
        super().setUp()
        self.finds = []
        self.answer = "codex-thread"

    def find_caller(self, tool, arguments):
        self.finds.append((tool, arguments))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def post(self, message, headers=None):
        body = json.dumps(message).encode()
        return mcp.handle(body, {} if headers is None else headers, self.store,
                          self.thread_project, self.find_caller)

    def test_without_a_header_the_finder_names_the_thread(self):
        is_error, _ = self.call("set_status", {"status": "G"})
        self.assertFalse(is_error)
        self.assertEqual(self.store.status("codex-thread").text, "G")
        self.assertEqual(self.finds, [("set_status", {"status": "G"})])
        self.assertEqual(self.asked, ["codex-thread"])

    def test_missing_arguments_are_looked_up_as_empty_arguments(self):
        self.call("set_status")
        self.assertEqual(self.finds, [("set_status", {})])

    def test_a_finder_error_is_a_tool_error_with_its_message(self):
        self.answer = CallerError("Two threads made the same call. Call again.")
        is_error, text = self.call("set_status", {"status": "G"})
        self.assertTrue(is_error)
        self.assertEqual(text, "Two threads made the same call. Call again.")
        self.assertEqual(self.store.statuses(), {})

    def test_a_header_wins_over_the_finder(self):
        self.call("set_status", {"status": "G"}, headers={"X-T3-Thread": THREAD})
        self.assertEqual(self.finds, [])
        self.assertEqual(self.store.status(THREAD).text, "G")

    def test_the_found_thread_is_checked_for_its_project(self):
        self.project_answer = False
        is_error, text = self.call("set_status", {"status": "G"})
        self.assertTrue(is_error)
        self.assertIn("another", text)


if __name__ == "__main__":
    unittest.main()
