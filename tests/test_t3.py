"""Tests of agentmap.t3, the read-only reader of T3 Code's database.

Each test builds a small SQLite database from tests/t3_fixture_schema.sql.
The tables have the shapes of T3 Code's statev2.sqlite, with only the
columns that the reader uses.
"""

import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from agentmap.events import Event
from agentmap.t3 import AgentThread, T3Reader, Touch

SCHEMA = (pathlib.Path(__file__).parent / "t3_fixture_schema.sql").read_text()

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
ROOT = "/work/proj"
PROJECT = "p-1"


def stamp(minutes_ago: float) -> str:
    """The T3 Code text form of the time `minutes_ago` minutes before NOW."""
    t = NOW - timedelta(minutes=minutes_ago)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Fixture:
    """A T3 Code database in a temporary directory, with one writer connection."""

    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "statev2.sqlite"
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        test.addCleanup(self.db.close)
        self._ordinal = 0
        self.project(PROJECT, ROOT)

    def project(self, project_id, root, deleted_at=None):
        self.db.execute("INSERT INTO projection_projects VALUES (?, ?, ?)",
                        (project_id, root, deleted_at))

    def thread(self, thread_id, *, project_id=PROJECT, title=None, parent=None,
               provider="claudeAgent", model="claude-opus-5-5", created=60.0,
               updated=1.0, worktree=None, forked_from=None, archived_at=None,
               deleted_at=None):
        payload = {
            "id": thread_id,
            "providerInstanceId": provider,
            "modelSelection": {"instanceId": provider, "model": model} if model else None,
            "worktreePath": worktree,
            "lineage": {"parentThreadId": parent} if parent else None,
        }
        if forked_from:
            payload["forkedFrom"] = {"type": "node", "nodeId": forked_from}
        self.db.execute(
            "INSERT INTO orchestration_v2_projection_threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, project_id, title or f"Title of {thread_id}", stamp(created),
             stamp(updated), archived_at, deleted_at, json.dumps(payload)))

    def touch(self, thread_id, updated):
        """Moves the thread's updated_at, as T3 Code does on every change of the thread."""
        self.db.execute(
            "UPDATE orchestration_v2_projection_threads SET updated_at = ? WHERE thread_id = ?",
            (stamp(updated), thread_id))

    def run(self, thread_id, ordinal, status, completed=None):
        """A run; `completed` is how many minutes before NOW it completed, or None."""
        run_id = f"run:{thread_id}:{ordinal}"
        self.db.execute(
            "INSERT INTO orchestration_v2_projection_runs (run_id, thread_id, ordinal, status,"
            " completed_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, thread_id, ordinal, status,
             stamp(completed) if completed is not None else None))
        return run_id

    def item(self, thread_id, type_, status="completed", run_id=None, node_id=None,
             ordinal=None, started=1.0, **payload):
        """A turn item that started `started` minutes before NOW."""
        self._ordinal += 1
        item_id = f"item-{self._ordinal}"
        payload = {"type": type_, "status": status, "startedAt": stamp(started), **payload}
        self.db.execute(
            "INSERT INTO orchestration_v2_projection_turn_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, thread_id, run_id, node_id, ordinal or self._ordinal, type_, status,
             json.dumps(payload)))
        return item_id

    def set_item_status(self, item_id, status):
        self.db.execute(
            "UPDATE orchestration_v2_projection_turn_items SET status = ? WHERE turn_item_id = ?",
            (status, item_id))

    def reader(self, test, root=ROOT, hours=3):
        reader = T3Reader(self.path, pathlib.Path(root), timedelta(hours=hours))
        test.addCleanup(reader.close)
        return reader


def by_id(threads):
    return {t.id: t for t in threads}


def paths(touched):
    return tuple(t.path for t in touched)


class SnapshotSelection(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)

    def test_snapshot_holds_the_project_threads_updated_inside_the_window(self):
        self.f.thread("recent", updated=10)
        self.f.thread("old", updated=4 * 60)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"recent"})

    def test_a_wider_window_holds_older_threads(self):
        self.f.thread("recent", updated=10)
        self.f.thread("old", updated=4 * 60)
        snap = by_id(self.f.reader(self, hours=48).snapshot(NOW))
        self.assertEqual(set(snap), {"recent", "old"})

    def test_snapshot_holds_the_ancestors_of_a_thread_in_the_window_so_the_tree_is_whole(self):
        self.f.thread("grandparent", updated=30 * 60)
        self.f.thread("parent", parent="grandparent", updated=20 * 60)
        self.f.thread("child", parent="parent", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"grandparent", "parent", "child"})
        self.assertEqual(snap["child"].parent_id, "parent")
        self.assertEqual(snap["parent"].parent_id, "grandparent")
        self.assertIsNone(snap["grandparent"].parent_id)

    def test_snapshot_skips_deleted_and_archived_threads(self):
        self.f.thread("live", updated=5)
        self.f.thread("deleted", updated=5, deleted_at=stamp(1))
        self.f.thread("archived", updated=5, archived_at=stamp(1))
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"live"})

    def test_a_child_whose_parent_is_deleted_becomes_a_root(self):
        self.f.thread("parent", updated=60 * 60, deleted_at=stamp(1))
        self.f.thread("child", parent="parent", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"child"})
        self.assertIsNone(snap["child"].parent_id)

    def test_every_project_id_with_the_watched_root_counts(self):
        self.f.project("p-2", ROOT)
        self.f.thread("a", project_id=PROJECT, updated=5)
        self.f.thread("b", project_id="p-2", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"a", "b"})

    def test_threads_of_another_root_or_of_a_deleted_project_are_not_in_the_snapshot(self):
        self.f.project("other", "/work/other")
        self.f.project("gone", ROOT, deleted_at=stamp(100))
        self.f.thread("mine", updated=5)
        self.f.thread("theirs", project_id="other", updated=5)
        self.f.thread("ghost", project_id="gone", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"mine"})

    def test_a_trailing_slash_on_the_project_root_does_not_matter(self):
        self.f.thread("a", updated=5)
        snap = by_id(self.f.reader(self, root=ROOT + "/").snapshot(NOW))
        self.assertEqual(set(snap), {"a"})

    def test_a_thread_carries_title_provider_model_and_aware_times(self):
        self.f.thread("c", title="Build it", provider="claudeAgent", model="claude-opus-5-5",
                      created=50, updated=5)
        self.f.thread("x", provider="codex", model=None, updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        c = snap["c"]
        self.assertIsInstance(c, AgentThread)
        self.assertEqual((c.title, c.provider, c.model), ("Build it", "claude", "claude-opus-5-5"))
        self.assertEqual(c.created_at, NOW - timedelta(minutes=50))
        self.assertEqual(c.updated_at, NOW - timedelta(minutes=5))
        self.assertIsNotNone(c.updated_at.tzinfo)
        self.assertEqual((snap["x"].provider, snap["x"].model), ("codex", None))

    def test_a_provider_instance_that_is_neither_claude_nor_codex_keeps_its_id(self):
        self.f.thread("g", provider="gemini", updated=5)
        self.f.thread("none", provider=None, updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual((snap["g"].provider, snap["none"].provider), ("gemini", ""))

    def test_a_named_provider_instance_maps_to_its_provider(self):
        self.f.thread("c2", provider="ClaudeAgent-work", updated=5)
        self.f.thread("x2", provider="Codex-home", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual((snap["c2"].provider, snap["x2"].provider), ("claude", "codex"))

    def test_a_thread_updated_exactly_at_the_window_start_is_inside(self):
        self.f.thread("edge", updated=3 * 60)
        self.f.thread("out", updated=3 * 60 + 0.001)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"edge"})

    def test_a_project_root_stored_with_a_trailing_slash_matches(self):
        self.f.project("p-slash", ROOT + "/")
        self.f.thread("a", project_id="p-slash", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"a"})

    def test_ancestors_of_several_threads_are_found_together(self):
        self.f.thread("p1", updated=30 * 60)
        self.f.thread("p2", updated=30 * 60)
        self.f.thread("c1", parent="p1", updated=5)
        self.f.thread("c2", parent="p2", updated=5)
        snap = by_id(self.f.reader(self).snapshot(NOW))
        self.assertEqual(set(snap), {"p1", "p2", "c1", "c2"})

    def test_threads_come_in_order_of_creation(self):
        self.f.thread("young", created=10, updated=5)
        self.f.thread("old", created=100, updated=5)
        self.f.thread("middle", created=50, updated=5)
        ids = [t.id for t in self.f.reader(self).snapshot(NOW)]
        self.assertEqual(ids, ["old", "middle", "young"])

    def test_a_naive_now_is_refused(self):
        with self.assertRaisesRegex(ValueError, "^now must be timezone-aware$"):
            self.f.reader(self).snapshot(NOW.replace(tzinfo=None))


class ThreadState(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("t", updated=1)

    def state(self):
        return by_id(self.f.reader(self).snapshot(NOW))["t"].state

    def test_the_state_comes_from_the_newest_run(self):
        self.f.run("t", 1, "failed")
        self.f.run("t", 2, "running")
        self.assertEqual(self.state(), "working")

    def test_a_queued_preparing_starting_running_or_waiting_run_means_working(self):
        for i, status in enumerate(["queued", "preparing", "starting", "running", "waiting"]):
            with self.subTest(status=status):
                self.f.run("t", 10 + i, status)
                self.assertEqual(self.state(), "working")

    def test_a_failed_newest_run_means_failed(self):
        self.f.run("t", 1, "running")
        self.f.run("t", 2, "failed")
        self.assertEqual(self.state(), "failed")

    def test_a_completed_interrupted_or_cancelled_run_means_idle(self):
        for i, status in enumerate(["completed", "interrupted", "cancelled"]):
            with self.subTest(status=status):
                self.f.run("t", 10 + i, status)
                self.assertEqual(self.state(), "idle")

    def test_a_thread_without_runs_or_subagent_item_is_idle(self):
        self.assertEqual(self.state(), "idle")

    def test_a_waiting_request_in_a_working_run_means_needs_you(self):
        for type_ in ["user_input_request", "approval_request", "secret_request"]:
            with self.subTest(type=type_):
                f = Fixture(self)
                f.thread("t", updated=1)
                run = f.run("t", 1, "running")
                f.item("t", "command_execution", "running", run, input="cargo test")
                f.item("t", type_, "waiting", run)
                self.assertEqual(by_id(f.reader(self).snapshot(NOW))["t"].state, "needs_you")

    def test_an_answered_request_does_not_mean_needs_you(self):
        run = self.f.run("t", 1, "running")
        self.f.item("t", "approval_request", "completed", run)
        self.assertEqual(self.state(), "working")

    def test_a_waiting_request_left_in_a_finished_run_does_not_mean_needs_you(self):
        run = self.f.run("t", 1, "interrupted")
        self.f.item("t", "approval_request", "waiting", run)
        self.assertEqual(self.state(), "idle")

    def test_a_waiting_request_of_an_older_run_does_not_mean_needs_you(self):
        old = self.f.run("t", 1, "interrupted")
        self.f.item("t", "approval_request", "waiting", old)
        self.f.run("t", 2, "running")
        self.assertEqual(self.state(), "working")


class NativeSubagentState(unittest.TestCase):
    """A subagent from Claude's Agent tool has no runs. Its parent's subagent item stands for its run."""

    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("parent", updated=1)
        parent_run = self.f.run("parent", 1, "completed")
        self.sub_item = self.f.item("parent", "subagent", "running", parent_run, node_id="node:task-1",
                                    title="Build t3.py reader", childThreadId="child")
        self.f.thread("child", parent="parent", forked_from="node:task-1", updated=1)

    def child(self, reader=None):
        return by_id((reader or self.f.reader(self)).snapshot(NOW))["child"]

    def test_a_running_subagent_item_means_the_subagent_is_working(self):
        self.assertEqual(self.child().state, "working")

    def test_a_failed_subagent_item_means_the_subagent_failed(self):
        self.f.set_item_status(self.sub_item, "failed")
        self.assertEqual(self.child().state, "failed")

    def test_a_completed_subagent_item_means_the_subagent_is_idle(self):
        self.f.set_item_status(self.sub_item, "completed")
        self.assertEqual(self.child().state, "idle")

    def test_a_subagent_whose_item_is_gone_is_idle(self):
        self.f.thread("orphan", parent="parent", forked_from="node:missing", updated=1)
        self.assertEqual(by_id(self.f.reader(self).snapshot(NOW))["orphan"].state, "idle")

    def test_a_waiting_request_of_a_working_subagent_means_needs_you(self):
        self.f.item("child", "user_input_request", "waiting")
        self.assertEqual(self.child().state, "needs_you")

    def test_the_subagent_state_follows_its_item_without_a_change_to_the_subagent_thread(self):
        reader = self.f.reader(self)
        self.assertEqual(self.child(reader).state, "working")
        self.f.set_item_status(self.sub_item, "completed")
        self.assertEqual(self.child(reader).state, "idle")


class NowLine(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("t", updated=1, worktree="/wt/t1")
        self.run_id = self.f.run("t", 1, "running")

    def now_of(self, type_, status="running", **payload):
        self.f.item("t", type_, status, self.run_id, **payload)
        return by_id(self.f.reader(self).snapshot(NOW))["t"].now

    def test_each_kind_of_active_item_has_its_own_now_line(self):
        cases = [
            (("command_execution",), {"input": "cargo test --release"}, "Running: cargo test --release"),
            (("file_change",), {"fileName": ROOT + "/src/render/cull.rs"}, "Editing src/render/cull.rs"),
            (("dynamic_tool",), {"toolName": "Read", "input": {"file_path": "/wt/t1/src/a.rs"}},
             "Reading src/a.rs"),
            (("dynamic_tool",), {"toolName": "Grep", "input": {"pattern": "fn cull"}}, "Searching fn cull"),
            (("dynamic_tool",), {"toolName": "Glob", "input": {"pattern": "**/*.rs"}}, "Searching **/*.rs"),
            (("dynamic_tool",), {"toolName": "mcp__t3-code__delegate_task",
                                 "input": {"title": "Fix the cull"}}, "Delegating: Fix the cull"),
            (("dynamic_tool",), {"toolName": "t3-code.delegate_task", "input": {"title": "Probe"}},
             "Delegating: Probe"),
            (("dynamic_tool",), {"toolName": "mcp__t3-code__t3_background_wait", "input": {}},
             "Waiting on background work"),
            (("dynamic_tool",), {"toolName": "t3-code.t3_thread_wait", "input": {}},
             "Waiting on background work"),
            (("dynamic_tool",), {"toolName": "mcp__blender__get_objects_summary", "input": {}},
             "Using get_objects_summary"),
            (("dynamic_tool",), {"toolName": "t3-code.html_render", "input": {}}, "Using html_render"),
            (("dynamic_tool",), {"toolName": "Skill", "input": {"skill": "x"}}, "Using Skill"),
            (("web_search",), {"patterns": ["fp16 rounding", "other"]}, "Searching the web: fp16 rounding"),
            (("reasoning",), {"text": "Hmm."}, "Thinking"),
            (("assistant_message",), {"text": "Done."}, "Writing a reply"),
            (("subagent",), {"title": "Build treemap.py"}, "Waiting on Build treemap.py"),
            (("user_input_request", "waiting"), {}, "Needs your answer"),
            (("approval_request", "waiting"), {}, "Needs your answer"),
            (("secret_request", "waiting"), {}, "Needs your answer"),
        ]
        for (type_, *status), payload, expected in cases:
            with self.subTest(type=type_, expected=expected):
                f = Fixture(self)
                f.thread("t", updated=1, worktree="/wt/t1")
                run_id = f.run("t", 1, "running")
                f.item("t", type_, status[0] if status else "running", run_id, **payload)
                self.assertEqual(by_id(f.reader(self).snapshot(NOW))["t"].now, expected)

    def test_a_bash_lc_wrapper_is_removed_from_a_command(self):
        self.assertEqual(self.now_of("command_execution",
                                     input="/etc/profiles/per-user/me/bin/bash -lc 'git remote -v'"),
                         "Running: git remote -v")

    def test_a_double_quoted_bash_lc_wrapper_is_removed_from_a_command(self):
        self.assertEqual(self.now_of("command_execution", input="bash -lc \"rg -n 'a|b' src\""),
                         "Running: rg -n 'a|b' src")

    def test_a_wrapped_command_with_unbalanced_quotes_loses_only_its_opening_quote(self):
        self.assertEqual(self.now_of("command_execution", input="bash -lc 'Xvfb :1 \"oops"),
                         "Running: Xvfb :1 \"oops")

    def test_an_item_without_the_detail_its_rule_needs_gets_a_general_line(self):
        cases = [
            ("command_execution", {}, "Running a command"),
            ("file_change", {}, "Editing a file"),
            ("web_search", {"patterns": []}, "Searching the web"),
            ("subagent", {}, "Waiting on a subagent"),
            ("dynamic_tool", {"input": {}}, "Using a tool"),
        ]
        for type_, payload, expected in cases:
            with self.subTest(type=type_):
                f = Fixture(self)
                f.thread("t", updated=1)
                run_id = f.run("t", 1, "running")
                f.item("t", type_, "running", run_id, **payload)
                self.assertEqual(by_id(f.reader(self).snapshot(NOW))["t"].now, expected)

    def test_a_tool_name_loses_only_its_server_prefix(self):
        cases = [("mcp__srv__do__it", "Using do__it"), ("srv.do.it", "Using do.it")]
        for name, expected in cases:
            with self.subTest(name=name):
                f = Fixture(self)
                f.thread("t", updated=1)
                run_id = f.run("t", 1, "running")
                f.item("t", "dynamic_tool", "running", run_id, toolName=name, input={})
                self.assertEqual(by_id(f.reader(self).snapshot(NOW))["t"].now, expected)

    def test_a_tool_with_a_title_input_is_not_a_delegation(self):
        self.assertEqual(self.now_of("dynamic_tool", toolName="mcp__t3-code__html_render",
                                     input={"title": "Open tickets", "html": "<p>"}),
                         "Using html_render")

    def test_a_waiting_subagent_item_does_not_mean_needs_you(self):
        self.assertEqual(self.now_of("subagent", "waiting", title="Probe"), "Waiting on Probe")
        self.assertEqual(by_id(self.f.reader(self).snapshot(NOW))["t"].state, "working")

    def test_an_empty_wrapped_command_gets_the_general_line(self):
        self.assertEqual(self.now_of("command_execution", input="bash -lc  "), "Running a command")

    def test_a_tool_whose_input_is_not_an_object_shows_its_name(self):
        self.assertEqual(self.now_of("dynamic_tool", toolName="Read", input="x.py"), "Using Read")

    def test_a_tool_without_the_input_its_rule_needs_shows_its_name(self):
        self.assertEqual(self.now_of("dynamic_tool", toolName="Grep", input={}), "Using Grep")

    def test_a_command_shows_only_its_first_line(self):
        self.assertEqual(self.now_of("command_execution", input="bash -lc 'cd src\ncargo build'"),
                         "Running: cd src")

    def test_a_command_shows_at_most_100_characters(self):
        now = self.now_of("command_execution", input="echo " + "x" * 200)
        self.assertTrue(now.startswith("Running: echo xxx"))
        self.assertEqual(len(now), len("Running: ") + 100)

    def test_a_short_command_is_not_cut(self):
        command = "echo " + "x" * 95
        self.assertEqual(self.now_of("command_execution", input=command), "Running: " + command)

    def test_the_now_line_comes_from_the_newest_active_item(self):
        self.f.item("t", "command_execution", "running", self.run_id, input="sleep 100")
        self.f.item("t", "reasoning", "running", self.run_id, text="x")
        self.f.item("t", "file_change", "completed", self.run_id, fileName=ROOT + "/a.rs")
        self.assertEqual(by_id(self.f.reader(self).snapshot(NOW))["t"].now, "Thinking")

    def test_the_now_line_is_none_when_no_item_is_active(self):
        self.assertIsNone(self.now_of("command_execution", "completed", input="ls"))

    def test_an_active_item_without_a_now_rule_is_passed_over(self):
        self.f.item("t", "command_execution", "running", self.run_id, input="make")
        self.f.item("t", "todo_list", "running", self.run_id, steps=[])
        self.assertEqual(by_id(self.f.reader(self).snapshot(NOW))["t"].now, "Running: make")

    def test_an_idle_thread_has_no_now_line_even_with_a_stale_running_item(self):
        f = Fixture(self)
        f.thread("t", updated=1)
        run_id = f.run("t", 1, "completed")
        f.item("t", "web_search", "running", run_id, patterns=["stale"])
        self.assertIsNone(by_id(f.reader(self).snapshot(NOW))["t"].now)

    def test_active_items_of_an_older_run_do_not_give_the_now_line(self):
        f = Fixture(self)
        f.thread("t", updated=1)
        old = f.run("t", 1, "completed")
        f.item("t", "web_search", "running", old, patterns=["stale"])
        f.run("t", 2, "running")
        self.assertIsNone(by_id(f.reader(self).snapshot(NOW))["t"].now)

    def test_editing_a_file_outside_both_roots_shows_its_full_path(self):
        self.assertEqual(self.now_of("file_change", fileName="/tmp/notes.md"), "Editing /tmp/notes.md")


class ThoughtAndLastSaid(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("t", updated=1)
        self.run_id = self.f.run("t", 1, "completed")

    def thread(self):
        return by_id(self.f.reader(self).snapshot(NOW))["t"]

    def test_the_thought_is_the_first_sentence_of_the_newest_thinking_text(self):
        self.f.item("t", "reasoning", text="Old idea. Gone.")
        self.f.item("t", "reasoning", text="\n\nI'm checking the cull pass. Then the lights.")
        self.f.item("t", "reasoning", text="")
        self.assertEqual(self.thread().thought, "I'm checking the cull pass.")

    def test_last_said_is_the_first_sentence_of_the_newest_assistant_message(self):
        self.f.item("t", "assistant_message", text="First reply.")
        self.f.item("t", "assistant_message", text="The build is green! Next I merge.")
        self.assertEqual(self.thread().last_said, "The build is green!")

    def test_a_question_ends_a_sentence(self):
        self.f.item("t", "assistant_message", text="Shall I merge? It is green.")
        self.assertEqual(self.thread().last_said, "Shall I merge?")

    def test_a_dot_inside_a_word_does_not_end_the_sentence(self):
        self.f.item("t", "assistant_message", text="I edited t3.py and ran it. Done.")
        self.assertEqual(self.thread().last_said, "I edited t3.py and ran it.")

    def test_text_without_a_sentence_end_ends_at_the_first_line(self):
        self.f.item("t", "assistant_message", text="**Plan**\nMore text.")
        self.assertEqual(self.thread().last_said, "**Plan**")

    def test_a_leading_harness_tag_block_is_skipped(self):
        self.f.item("t", "assistant_message",
                    text="<tools-used-before-summary>Edit, Skill</tools-used-before-summary>\n\n"
                         "(•̀ᴗ•́)و The reader is built. All tests pass.")
        self.assertEqual(self.thread().last_said, "(•̀ᴗ•́)و The reader is built.")

    def test_several_leading_tag_blocks_over_several_lines_are_skipped(self):
        self.f.item("t", "assistant_message",
                    text="<a-b>x\ny</a-b> <c>z</c>\n**Report: the Tao** of it.")
        self.assertEqual(self.thread().last_said, "**Report: the Tao** of it.")

    def test_a_tag_inside_the_sentence_stays(self):
        self.f.item("t", "assistant_message", text="Use <b>bold</b> here. Then stop.")
        self.assertEqual(self.thread().last_said, "Use <b>bold</b> here.")

    def test_a_text_of_only_a_tag_block_gives_none(self):
        self.f.item("t", "assistant_message", text="<tools-used-before-summary>Read</tools-used-before-summary>")
        self.assertIsNone(self.thread().last_said)

    def test_a_long_sentence_is_cut_to_160_characters(self):
        self.f.item("t", "reasoning", text="word " * 100)
        thought = self.thread().thought
        self.assertEqual(len(thought), 160)
        self.assertTrue(thought.endswith("…"))

    def test_a_sentence_of_exactly_160_characters_is_kept_whole(self):
        text = "a" * 159 + "."
        self.f.item("t", "assistant_message", text=text)
        self.assertEqual(self.thread().last_said, text)

    def test_text_of_only_unusual_white_space_gives_none(self):
        self.f.item("t", "assistant_message", text=" ")
        self.assertIsNone(self.thread().last_said)

    def test_text_of_only_line_breaks_is_passed_over_for_an_older_text(self):
        self.f.item("t", "reasoning", text="Older thought. More.")
        self.f.item("t", "reasoning", text="\n\t\r\n ")
        self.assertEqual(self.thread().thought, "Older thought.")

    def test_without_thinking_or_messages_both_are_none(self):
        thread = self.thread()
        self.assertIsNone(thread.thought)
        self.assertIsNone(thread.last_said)

    def test_thought_at_is_the_start_time_of_the_thinking_item_the_thought_came_from(self):
        self.f.item("t", "reasoning", started=9, text="Old idea.")
        self.f.item("t", "reasoning", started=4, text="New idea.")
        self.f.item("t", "reasoning", started=2, text="  ")
        self.assertEqual(self.thread().thought_at, NOW - timedelta(minutes=4))

    def test_said_at_is_the_start_time_of_the_message_last_said_came_from(self):
        self.f.item("t", "assistant_message", started=7, text="Old reply.")
        self.f.item("t", "assistant_message", started=3, text="New reply.")
        self.f.item("t", "reasoning", started=1, text="A later thought.")
        thread = self.thread()
        self.assertEqual(thread.said_at, NOW - timedelta(minutes=3))
        self.assertEqual(thread.said_at.utcoffset(), timedelta(0))

    def test_without_thinking_or_messages_both_times_are_none(self):
        thread = self.thread()
        self.assertIsNone(thread.thought_at)
        self.assertIsNone(thread.said_at)

    def test_a_text_that_gives_no_sentence_gives_no_time(self):
        self.f.item("t", "reasoning", text="<note>only a tag block</note>")
        self.f.item("t", "assistant_message", text="<note>only a tag block</note>")
        thread = self.thread()
        self.assertIsNone(thread.thought_at)
        self.assertIsNone(thread.said_at)


class TouchedPaths(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("t", updated=1, worktree="/wt/t1")

    def touched(self):
        return paths(by_id(self.f.reader(self).snapshot(NOW))["t"].touched)

    def test_paths_in_the_project_root_become_relative_newest_first_without_repeats(self):
        self.f.item("t", "file_change", fileName=ROOT + "/src/a.rs")
        self.f.item("t", "file_change", fileName=ROOT + "/src/b.rs")
        self.f.item("t", "dynamic_tool", toolName="Read", input={"file_path": ROOT + "/src/a.rs"})
        self.assertEqual(self.touched(), ("src/a.rs", "src/b.rs"))

    def test_paths_in_the_thread_worktree_become_relative_to_the_worktree(self):
        self.f.item("t", "file_change", fileName="/wt/t1/src/render/cull.rs")
        self.assertEqual(self.touched(), ("src/render/cull.rs",))

    def test_a_path_outside_both_roots_is_not_a_touched_path(self):
        self.f.item("t", "file_change", fileName="/tmp/scratch.py")
        self.f.item("t", "file_change", fileName="/work/projector/x.py")
        self.f.item("t", "file_change", fileName="relative/x.py")
        self.assertEqual(self.touched(), ())

    def test_the_root_itself_is_not_a_touched_path(self):
        self.f.item("t", "dynamic_tool", toolName="Grep", input={"pattern": "x", "path": ROOT})
        self.assertEqual(self.touched(), ())

    def test_dynamic_tools_touch_their_file_path_path_or_notebook_path(self):
        self.f.item("t", "dynamic_tool", toolName="Read", input={"file_path": ROOT + "/a.py"})
        self.f.item("t", "dynamic_tool", toolName="Grep", input={"pattern": "x", "path": ROOT + "/src"})
        self.f.item("t", "dynamic_tool", toolName="NotebookEdit",
                    input={"notebook_path": ROOT + "/n.ipynb"})
        self.f.item("t", "dynamic_tool", toolName="Skill", input={"skill": "x"})
        self.assertEqual(self.touched(), ("n.ipynb", "src", "a.py"))

    def test_other_item_types_touch_nothing(self):
        self.f.item("t", "command_execution", input=f"cat {ROOT}/a.py", fileName=ROOT + "/a.py")
        self.assertEqual(self.touched(), ())

    def test_at_most_8_touched_paths_are_kept(self):
        for i in range(12):
            self.f.item("t", "file_change", fileName=f"{ROOT}/f{i}.rs")
        self.assertEqual(self.touched(), tuple(f"f{i}.rs" for i in range(11, 3, -1)))

    def test_a_claude_agent_worktree_inside_the_root_is_taken_off_the_path(self):
        self.f.item("t", "file_change", fileName=ROOT + "/.claude/worktrees/agent-a1/agentmap/t3.py")
        self.assertEqual(self.touched(), ("agentmap/t3.py",))

    def test_a_touched_path_carries_the_start_time_of_the_newest_item_that_touched_it(self):
        self.f.item("t", "file_change", started=30, fileName=ROOT + "/src/a.rs")
        self.f.item("t", "file_change", started=20, fileName=ROOT + "/src/b.rs")
        self.f.item("t", "dynamic_tool", started=10, toolName="Read",
                    input={"file_path": ROOT + "/src/a.rs"})
        touched = by_id(self.f.reader(self).snapshot(NOW))["t"].touched
        self.assertEqual(touched, (Touch("src/a.rs", NOW - timedelta(minutes=10)),
                                   Touch("src/b.rs", NOW - timedelta(minutes=20))))
        self.assertEqual(touched[0].at.utcoffset(), timedelta(0))

    def test_dots_in_a_path_are_resolved_before_the_root_check(self):
        self.f.item("t", "file_change", fileName=ROOT + "/src/../lib/x.rs")
        self.f.item("t", "file_change", fileName=ROOT + "/../elsewhere/y.rs")
        self.assertEqual(self.touched(), ("lib/x.rs",))


class ChangeDetection(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("t", updated=5)
        self.run_id = self.f.run("t", 1, "running")

    def test_changed_is_true_on_the_first_call(self):
        self.assertTrue(self.f.reader(self).changed())

    def test_changed_is_false_when_nothing_was_written(self):
        reader = self.f.reader(self)
        reader.changed()
        reader.snapshot(NOW)
        self.assertFalse(reader.changed())

    def test_changed_is_true_after_t3_code_commits_a_write(self):
        reader = self.f.reader(self)
        reader.changed()
        self.f.item("t", "reasoning", "running", self.run_id, text="x")
        self.assertTrue(reader.changed())
        self.assertFalse(reader.changed())

    def test_a_new_item_shows_once_the_thread_updated_at_moves(self):
        reader = self.f.reader(self)
        self.assertIsNone(by_id(reader.snapshot(NOW))["t"].now)
        self.f.item("t", "reasoning", "running", self.run_id, text="x")
        self.f.touch("t", updated=1)
        self.assertEqual(by_id(reader.snapshot(NOW))["t"].now, "Thinking")

    def test_the_turn_items_of_an_unchanged_thread_are_not_read_again(self):
        reader = self.f.reader(self)
        reader.snapshot(NOW)
        # A write that T3 Code never makes: a new item without a move of updated_at.
        self.f.item("t", "reasoning", "running", self.run_id, text="x")
        self.assertIsNone(by_id(reader.snapshot(NOW))["t"].now)

    def test_touched_paths_stay_newest_first_without_repeats_across_snapshots(self):
        reader = self.f.reader(self)
        for i in range(6):
            self.f.item("t", "file_change", fileName=f"{ROOT}/old{i}.rs")
        self.f.touch("t", updated=4)
        self.assertEqual(paths(by_id(reader.snapshot(NOW))["t"].touched),
                         tuple(f"old{i}.rs" for i in range(5, -1, -1)))
        self.f.item("t", "file_change", fileName=f"{ROOT}/old0.rs")
        self.f.item("t", "dynamic_tool", toolName="Read", input={"file_path": f"{ROOT}/new.rs"})
        self.f.item("t", "dynamic_tool", toolName="Skill", input={})
        self.f.item("t", "file_change", fileName=f"{ROOT}/old0.rs")
        self.f.touch("t", updated=3)
        self.assertEqual(paths(by_id(reader.snapshot(NOW))["t"].touched),
                         ("old0.rs", "new.rs", "old5.rs", "old4.rs", "old3.rs", "old2.rs",
                          "old1.rs"))
        for i in range(9):
            self.f.item("t", "file_change", fileName=f"{ROOT}/n{i}.rs")
        self.f.touch("t", updated=2)
        self.assertEqual(paths(by_id(reader.snapshot(NOW))["t"].touched),
                         tuple(f"n{i}.rs" for i in range(8, 0, -1)))

    def test_the_touched_scan_reads_only_items_newer_than_those_it_has_seen(self):
        # T3 Code appends items with growing ordinals. An item that appears below the
        # newest seen ordinal is never read; that keeps a change of a long thread cheap.
        reader = self.f.reader(self)
        self.f.item("t", "file_change", fileName=f"{ROOT}/seen.rs", ordinal=100)
        self.f.touch("t", updated=4)
        reader.snapshot(NOW)
        self.f.item("t", "file_change", fileName=f"{ROOT}/below.rs", ordinal=50)
        self.f.item("t", "file_change", fileName=f"{ROOT}/above.rs", ordinal=200)
        self.f.touch("t", updated=3)
        self.assertEqual(paths(by_id(reader.snapshot(NOW))["t"].touched), ("above.rs", "seen.rs"))

    def test_a_path_touched_again_after_a_snapshot_carries_its_new_time(self):
        reader = self.f.reader(self)
        self.f.item("t", "file_change", started=30, fileName=f"{ROOT}/a.rs")
        self.f.item("t", "file_change", started=20, fileName=f"{ROOT}/b.rs")
        self.f.touch("t", updated=4)
        reader.snapshot(NOW)
        self.f.item("t", "file_change", started=2, fileName=f"{ROOT}/a.rs")
        self.f.touch("t", updated=2)
        self.assertEqual(by_id(reader.snapshot(NOW))["t"].touched,
                         (Touch("a.rs", NOW - timedelta(minutes=2)),
                          Touch("b.rs", NOW - timedelta(minutes=20))))

    def test_a_new_run_is_seen_even_when_updated_at_did_not_move(self):
        reader = self.f.reader(self)
        self.assertEqual(by_id(reader.snapshot(NOW))["t"].state, "working")
        self.f.run("t", 2, "failed")
        self.assertEqual(by_id(reader.snapshot(NOW))["t"].state, "failed")


def minutes(n):
    """The time `n` minutes before NOW."""
    return NOW - timedelta(minutes=n)


def stamp_at(t):
    """The T3 Code text form of the time `t`."""
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Events(unittest.TestCase):
    """events() lists what the project's threads did inside the window, newest first."""

    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("t", title="Cull lights", created=200, updated=1, worktree="/wt/t1")

    def events(self, reader=None, now=NOW):
        return (reader or self.f.reader(self)).events(now)

    def kinds(self, kind, reader=None):
        return [e for e in self.events(reader) if e.kind == kind]

    def test_a_thread_created_inside_the_window_gives_a_started_event_with_its_title(self):
        self.f.thread("new", title="Fix the cull", created=50, updated=1)
        self.assertEqual(self.kinds("started"),
                         [Event(minutes(50), "new", "started", "Fix the cull")])

    def test_a_file_change_gives_an_edits_event_at_its_start_with_its_relative_path(self):
        self.f.item("t", "file_change", started=12, fileName=ROOT + "/src/a.rs")
        self.f.item("t", "file_change", started=8, fileName="/wt/t1/src/b.rs")
        self.assertEqual(self.kinds("edits"), [Event(minutes(8), "t", "edits", "src/b.rs"),
                                               Event(minutes(12), "t", "edits", "src/a.rs")])

    def test_a_file_change_outside_both_roots_or_without_a_path_gives_no_event(self):
        self.f.item("t", "file_change", fileName="/tmp/scratch.py")
        self.f.item("t", "file_change")
        self.assertEqual(self.kinds("edits"), [])

    def test_reading_a_file_or_running_a_command_gives_no_event(self):
        self.f.item("t", "dynamic_tool", toolName="Read", input={"file_path": ROOT + "/a.py"})
        self.f.item("t", "command_execution", input="ls")
        self.assertEqual(self.events(), [])

    def test_a_question_asks_with_the_first_sentence_of_its_first_question(self):
        self.f.item("t", "user_input_request", started=5, questions=[
            {"header": "Direction", "question": "Which mockup wins? Pick one.", "options": []},
            {"header": "Other", "question": "Second question?", "options": []}])
        self.assertEqual(self.events(), [Event(minutes(5), "t", "asks", "Which mockup wins?")])

    def test_an_approval_request_asks_with_the_first_sentence_of_its_prompt(self):
        self.f.item("t", "approval_request", "waiting", requestKind="command",
                    prompt="git commit -qm x\ngit log", reason="It writes.")
        self.assertEqual([e.text for e in self.kinds("asks")], ["git commit -qm x"])

    def test_a_secret_request_asks_with_its_label(self):
        self.f.item("t", "secret_request", label="New Runpod API key. Paste it.",
                    reason="The old key expired.")
        self.assertEqual([e.text for e in self.kinds("asks")], ["New Runpod API key."])

    def test_a_request_without_question_text_asks_with_empty_text(self):
        self.f.item("t", "user_input_request", questions=[])
        self.f.item("t", "user_input_request", questions=[{"header": "No question"}])
        self.f.item("t", "user_input_request")
        self.f.item("t", "approval_request", prompt=None)
        self.f.item("t", "secret_request", label="  ")
        self.assertEqual([e.text for e in self.kinds("asks")], [""] * 5)

    def test_a_completed_run_finishes_at_its_completion_with_its_newest_message(self):
        run = self.f.run("t", 1, "completed", completed=6)
        self.f.item("t", "assistant_message", run_id=run, text="Early words.")
        self.f.item("t", "assistant_message", run_id=run,
                    text="<tools-used>Edit</tools-used>The cull is done. Tests pass.")
        self.f.item("t", "assistant_message", run_id=run, text=" ")
        self.assertEqual(self.kinds("finished"),
                         [Event(minutes(6), "t", "finished", "The cull is done.")])

    def test_a_failed_run_gives_a_failed_event(self):
        run = self.f.run("t", 1, "failed", completed=6)
        self.f.item("t", "assistant_message", run_id=run, text="I hit a wall.")
        self.assertEqual(self.kinds("failed"),
                         [Event(minutes(6), "t", "failed", "I hit a wall.")])

    def test_a_run_without_a_message_finishes_with_empty_text(self):
        self.f.run("t", 1, "completed", completed=6)
        self.f.item("t", "assistant_message", text="A message of no run.")
        self.assertEqual([e.text for e in self.kinds("finished")], [""])

    def test_the_finished_text_comes_from_that_run_not_a_later_one(self):
        first = self.f.run("t", 1, "completed", completed=20)
        self.f.item("t", "assistant_message", run_id=first, text="First run done.")
        second = self.f.run("t", 2, "running")
        self.f.item("t", "assistant_message", run_id=second, text="Second run talking.")
        self.assertEqual([e.text for e in self.kinds("finished")], ["First run done."])

    def test_running_interrupted_or_cancelled_runs_give_no_end_event(self):
        self.f.run("t", 1, "interrupted", completed=9)
        self.f.run("t", 2, "cancelled", completed=8)
        self.f.run("t", 3, "running")
        self.assertEqual(self.events(), [])

    def test_a_native_subagent_finishes_when_its_parent_subagent_item_completes(self):
        self.f.thread("sub", parent="t", forked_from="node:task-1", created=300, updated=2)
        self.f.item("t", "subagent", "completed", node_id="node:task-1", started=30,
                    completedAt=stamp(4))
        self.f.item("sub", "assistant_message", text="Old reply.")
        self.f.item("sub", "assistant_message", text="Reader built. All green.")
        self.assertEqual(self.kinds("finished"),
                         [Event(minutes(4), "sub", "finished", "Reader built.")])

    def test_a_native_subagent_whose_item_failed_gives_a_failed_event(self):
        self.f.thread("sub", parent="t", forked_from="node:task-1", created=300, updated=2)
        self.f.item("t", "subagent", "failed", node_id="node:task-1", completedAt=stamp(4))
        self.assertEqual(self.kinds("failed"), [Event(minutes(4), "sub", "failed", "")])

    def test_a_native_subagent_still_running_or_without_an_end_time_gives_no_end_event(self):
        self.f.thread("sub", parent="t", forked_from="node:a", created=300, updated=2)
        self.f.thread("sub2", parent="t", forked_from="node:b", created=300, updated=2)
        self.f.thread("sub3", parent="t", forked_from="node:gone", created=300, updated=2)
        self.f.item("t", "subagent", "running", node_id="node:a")
        self.f.item("t", "subagent", "completed", node_id="node:b")
        self.assertEqual(self.events(), [])

    def test_a_native_subagent_end_shows_without_a_change_to_the_subagent_thread(self):
        self.f.thread("sub", parent="t", forked_from="node:task-1", created=300, updated=2)
        item = self.f.item("t", "subagent", "running", node_id="node:task-1")
        reader = self.f.reader(self)
        self.assertEqual(self.kinds("finished", reader), [])
        self.f.set_item_status(item, "completed")
        self.f.db.execute(
            "UPDATE orchestration_v2_projection_turn_items"
            " SET payload_json = json_set(payload_json, '$.completedAt', ?)"
            " WHERE turn_item_id = ?", (stamp(1), item))
        self.assertEqual(self.kinds("finished", reader),
                         [Event(minutes(1), "sub", "finished", "")])

    def test_a_thread_with_a_run_is_not_ended_by_a_subagent_item(self):
        self.f.thread("sub", parent="t", forked_from="node:task-1", created=300, updated=2)
        self.f.run("sub", 1, "running")
        self.f.item("t", "subagent", "completed", node_id="node:task-1", completedAt=stamp(4))
        self.assertEqual(self.events(), [])

    def test_events_before_the_window_or_after_now_are_left_out(self):
        self.f.item("t", "file_change", started=3 * 60 + 1, fileName=ROOT + "/old.rs")
        self.f.item("t", "file_change", started=3 * 60, fileName=ROOT + "/edge.rs")
        self.f.item("t", "file_change", started=-1, fileName=ROOT + "/future.rs")
        self.f.run("t", 1, "completed", completed=3 * 60 + 1)
        self.f.run("t", 2, "completed", completed=-1)
        self.assertEqual(self.events(), [Event(minutes(3 * 60), "t", "edits", "edge.rs")])

    def test_events_hold_only_the_threads_of_the_snapshot(self):
        self.f.project("other", "/work/other")
        self.f.thread("theirs", project_id="other", created=10, updated=1)
        self.f.thread("stale", created=10, updated=4 * 60)
        self.f.thread("archived", created=10, updated=1, archived_at=stamp(1))
        self.f.item("theirs", "file_change", fileName="/work/other/x.rs")
        self.assertEqual(self.events(), [])

    def test_an_ancestor_outside_the_window_still_gives_its_events_inside_the_window(self):
        self.f.thread("old-root", created=5, updated=5 * 60)
        self.f.thread("kid", parent="old-root", created=300, updated=1)
        self.assertEqual(self.events(),
                         [Event(minutes(5), "old-root", "started", "Title of old-root")])

    def test_events_come_newest_first_from_all_threads(self):
        self.f.thread("u", title="Other", created=15, updated=1)
        self.f.item("t", "file_change", started=20, fileName=ROOT + "/a.rs")
        self.f.item("u", "approval_request", started=10, prompt="May I?")
        self.f.run("t", 1, "completed", completed=5)
        self.assertEqual([(e.at, e.thread_id, e.kind) for e in self.events()], [
            (minutes(5), "t", "finished"), (minutes(10), "u", "asks"),
            (minutes(15), "u", "started"), (minutes(20), "t", "edits")])

    def test_events_at_the_same_time_come_in_a_fixed_order(self):
        self.f.thread("u", created=200, updated=1)
        for thread in ("t", "u"):
            self.f.item(thread, "file_change", started=5, fileName=ROOT + "/b.rs")
            self.f.item(thread, "file_change", started=5, fileName=ROOT + "/a.rs")
            self.f.item(thread, "secret_request", started=5, label="Key.")
        self.assertEqual([(e.thread_id, e.kind, e.text) for e in self.events()], [
            ("u", "asks", "Key."), ("u", "edits", "b.rs"), ("u", "edits", "a.rs"),
            ("t", "asks", "Key."), ("t", "edits", "b.rs"), ("t", "edits", "a.rs")])

    def test_at_most_80_events_are_given(self):
        for i in range(90):
            self.f.item("t", "file_change", started=100 - i, fileName=f"{ROOT}/f{i}.rs")
        events = self.events()
        self.assertEqual(len(events), 80)
        self.assertEqual((events[0].text, events[-1].text), ("f89.rs", "f10.rs"))

    def test_a_naive_now_is_refused(self):
        with self.assertRaisesRegex(ValueError, "^now must be timezone-aware$"):
            self.f.reader(self).events(datetime(2026, 11, 14, 15, 0))

    def test_a_new_item_shows_once_the_thread_updated_at_moves(self):
        reader = self.f.reader(self)
        self.assertEqual(self.events(reader), [])
        self.f.item("t", "file_change", started=1, fileName=ROOT + "/a.rs")
        self.assertEqual(self.events(reader), [])
        self.f.touch("t", updated=0.5)
        self.assertEqual([e.text for e in self.events(reader)], ["a.rs"])

    def test_a_new_run_end_shows_once_the_thread_updated_at_moves(self):
        reader = self.f.reader(self)
        self.events(reader)
        self.f.run("t", 1, "completed", completed=1)
        self.f.touch("t", updated=0.5)
        self.assertEqual([e.kind for e in self.events(reader)], ["finished"])

    def test_the_item_scan_keeps_what_it_found_and_reads_only_newer_items(self):
        reader = self.f.reader(self)
        self.f.item("t", "file_change", started=9, fileName=f"{ROOT}/seen.rs", ordinal=100)
        self.f.touch("t", updated=0.8)
        self.events(reader)
        self.f.item("t", "file_change", started=8, fileName=f"{ROOT}/below.rs", ordinal=50)
        self.f.item("t", "file_change", started=7, fileName=f"{ROOT}/above.rs", ordinal=200)
        self.f.touch("t", updated=0.5)
        self.assertEqual([e.text for e in self.events(reader)], ["above.rs", "seen.rs"])

    def test_an_event_exactly_at_the_window_start_stays_after_the_thread_changes(self):
        reader = self.f.reader(self)
        self.f.item("t", "file_change", started=3 * 60, fileName=f"{ROOT}/edge.rs")
        self.events(reader)
        self.f.touch("t", updated=0.5)
        self.assertEqual([e.text for e in self.events(reader)], ["edge.rs"])

    def test_an_item_from_before_the_window_does_not_end_the_scan(self):
        # The scan does not rely on start times that grow with the ordinal.
        self.f.item("t", "file_change", started=10, fileName=f"{ROOT}/inside.rs", ordinal=100)
        self.f.item("t", "file_change", started=5 * 60, fileName=f"{ROOT}/before.rs", ordinal=200)
        self.assertEqual([e.text for e in self.events()], ["inside.rs"])

    def test_an_event_drops_out_once_the_window_moves_past_it(self):
        reader = self.f.reader(self)
        self.f.item("t", "file_change", started=60, fileName=f"{ROOT}/early.rs")
        self.f.item("t", "file_change", started=10, fileName=f"{ROOT}/late.rs")
        self.assertEqual(len(self.events(reader)), 2)
        later = NOW + timedelta(hours=2, minutes=30)
        self.f.item("t", "file_change", started=-150, fileName=f"{ROOT}/new.rs")
        self.f.db.execute("UPDATE orchestration_v2_projection_threads SET updated_at = ?",
                          (stamp_at(later),))
        self.assertEqual([e.text for e in self.events(reader, later)], ["new.rs", "late.rs"])

    def test_a_thread_that_left_the_window_is_forgotten(self):
        reader = self.f.reader(self)
        self.f.item("t", "file_change", started=10, fileName=f"{ROOT}/a.rs")
        self.events(reader)
        self.assertEqual(self.events(reader, NOW + timedelta(hours=4)), [])
        self.assertEqual(reader._logs, {})


class ThreadProject(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.f.project("other", "/work/other")
        self.f.thread("mine", updated=10 * 24 * 60)
        self.f.thread("theirs", project_id="other")

    def test_a_thread_of_the_watched_project_gives_true(self):
        self.assertIs(self.f.reader(self).thread_project("mine"), True)

    def test_a_thread_of_another_project_gives_false(self):
        self.assertIs(self.f.reader(self).thread_project("theirs"), False)

    def test_an_unknown_thread_gives_none(self):
        self.assertIsNone(self.f.reader(self).thread_project("nobody"))

    def test_a_thread_that_appears_later_is_found(self):
        reader = self.f.reader(self)
        self.assertIsNone(reader.thread_project("late"))
        self.f.thread("late")
        self.assertIs(reader.thread_project("late"), True)

    def test_thread_project_works_from_another_python_thread(self):
        reader = self.f.reader(self)
        reader.snapshot(NOW)
        result = []
        worker = threading.Thread(target=lambda: result.append(reader.thread_project("mine")))
        worker.start()
        worker.join()
        self.assertEqual(result, [True])


class ReadOnly(unittest.TestCase):
    def test_the_reader_cannot_write_to_the_database(self):
        f = Fixture(self)
        reader = f.reader(self)
        with self.assertRaises(sqlite3.OperationalError) as caught:
            reader._conn.execute("CREATE TABLE intruder (x)")
        self.assertIn("readonly", str(caught.exception))

    def test_a_missing_database_is_not_created(self):
        missing = pathlib.Path(tempfile.mkdtemp()) / "none.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            T3Reader(missing, pathlib.Path(ROOT), timedelta(hours=3))
        self.assertFalse(missing.exists())


class FindToolCalls(unittest.TestCase):
    """The database side of finding the thread that made an MCP call."""

    def setUp(self):
        self.f = Fixture(self)
        self.f.thread("claude-t")
        self.f.thread("codex-t", provider="codex")

    def call(self, thread, tool_name, input, status="running", started=0.1):
        return self.f.item(thread, "dynamic_tool", status=status, toolName=tool_name,
                           input=input, startedAt=stamp(started))

    def find(self, tool, arguments):
        return self.f.reader(self).find_tool_calls(tool, arguments, NOW)

    def test_a_running_claude_call_of_the_agentmap_tool_is_found(self):
        item = self.call("claude-t", "mcp__agentmap__set_goal", {"goal": "G"})
        self.assertEqual(self.find("set_goal", {"goal": "G"}), [(item, "claude-t")])

    def test_a_running_codex_call_of_the_agentmap_tool_is_found(self):
        item = self.call("codex-t", "agentmap.set_goal", {"goal": "G"})
        self.assertEqual(self.find("set_goal", {"goal": "G"}), [(item, "codex-t")])

    def test_calls_of_other_servers_other_tools_or_other_arguments_are_not_found(self):
        self.call("claude-t", "mcp__other__set_goal", {"goal": "G"})
        self.call("codex-t", "other.set_goal", {"goal": "G"})
        self.call("claude-t", "mcp__agentmap__set_status", {"goal": "G"})
        self.call("codex-t", "agentmap.set_goal", {"goal": "H"})
        self.assertEqual(self.find("set_goal", {"goal": "G"}), [])

    def test_argument_order_does_not_matter(self):
        item = self.call("codex-t", "agentmap.mark_todo", {"state": "done", "id": "1"})
        self.assertEqual(self.find("mark_todo", {"id": "1", "state": "done"}), [(item, "codex-t")])

    def test_a_call_without_input_matches_empty_arguments(self):
        item = self.call("codex-t", "agentmap.get_plan", None)
        self.assertEqual(self.find("get_plan", {}), [(item, "codex-t")])

    def test_finished_calls_are_not_found(self):
        for status in ("completed", "failed", "interrupted", "cancelled"):
            self.call("codex-t", "agentmap.get_plan", {}, status=status)
        self.assertEqual(self.find("get_plan", {}), [])

    def test_pending_and_waiting_calls_are_found(self):
        a = self.call("codex-t", "agentmap.get_plan", {}, status="pending", started=0.3)
        b = self.call("claude-t", "mcp__agentmap__get_plan", {}, status="waiting", started=0.2)
        self.assertEqual(self.find("get_plan", {}), [(a, "codex-t"), (b, "claude-t")])

    def test_a_call_started_exactly_five_minutes_ago_still_counts(self):
        item = self.call("codex-t", "agentmap.get_plan", {}, started=5.0)
        self.assertEqual(self.find("get_plan", {}), [(item, "codex-t")])

    def test_calls_started_over_five_minutes_ago_are_stale(self):
        self.call("codex-t", "agentmap.get_plan", {}, started=5.1)
        fresh = self.call("codex-t", "agentmap.get_plan", {}, started=4.9)
        self.assertEqual(self.find("get_plan", {}), [(fresh, "codex-t")])

    def test_calls_are_listed_oldest_first(self):
        new = self.call("codex-t", "agentmap.get_plan", {}, started=0.1)
        old = self.call("claude-t", "mcp__agentmap__get_plan", {}, started=0.5)
        self.assertEqual(self.find("get_plan", {}), [(old, "claude-t"), (new, "codex-t")])

    def test_a_call_without_a_start_time_is_skipped(self):
        self.f.item("codex-t", "dynamic_tool", status="running", toolName="agentmap.get_plan",
                    input={}, startedAt=None)
        self.assertEqual(self.find("get_plan", {}), [])


if __name__ == "__main__":
    unittest.main()
