"""Behavior of the plan store: plans of agent threads, kept in an append-only log."""

import contextlib
import io
import json
import pathlib
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

from agentmap import plan as plan_module
from agentmap.events import Event
from agentmap.plan import Item, Plan, PlanError, PlanStore

T1 = "thread-one"
T2 = "thread-two"


def at(second):
    """A fixed UTC time, `second` seconds after 14:40:00."""
    return datetime(2026, 11, 14, 14, 40, second, tzinfo=timezone.utc)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        self.log = self.root / ".agentmap" / "plan.jsonl"
        self.store = PlanStore(self.log)

    def reopen(self):
        return PlanStore(self.log)

    def log_records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def states(self, thread=T1):
        """The ids and states of all items of one plan, in outline order."""
        out = []
        for item in self.store.plan(thread).items:
            out.append((item.id, item.state))
            out.extend((child.id, child.state) for child in item.children)
        return out


class GoalAndStatus(StoreTestCase):
    def test_a_new_store_over_a_missing_log_has_no_plans(self):
        self.assertEqual(self.store.plans(), {})
        self.assertIsNone(self.store.plan(T1))

    def test_set_goal_creates_a_plan_with_the_stripped_goal(self):
        self.store.set_goal(T1, "  Cap lights per tile.  \n")
        plan = self.store.plan(T1)
        self.assertEqual(plan.goal, "Cap lights per tile.")
        self.assertIsNone(plan.status)
        self.assertEqual(plan.items, ())

    def test_set_status_sets_the_status_line_and_keeps_the_goal(self):
        self.store.set_goal(T1, "Goal")
        self.store.set_status(T1, " Waiting on the timed take. ")
        plan = self.store.plan(T1)
        self.assertEqual(plan.goal, "Goal")
        self.assertEqual(plan.status, "Waiting on the timed take.")

    def test_a_status_before_any_goal_creates_a_plan_without_a_goal(self):
        self.store.set_status(T1, "Reading")
        self.assertIsNone(self.store.plan(T1).goal)
        self.assertEqual(self.store.plan(T1).status, "Reading")

    def test_a_new_goal_replaces_the_old_goal(self):
        self.store.set_goal(T1, "Old")
        self.store.set_goal(T1, "New")
        self.assertEqual(self.store.plan(T1).goal, "New")

    def test_each_thread_has_its_own_plan(self):
        self.store.set_goal(T1, "One")
        self.store.set_goal(T2, "Two")
        self.assertEqual(self.store.plan(T1).goal, "One")
        self.assertEqual(self.store.plan(T2).goal, "Two")
        self.assertEqual(set(self.store.plans()), {T1, T2})

    def test_plans_returns_a_copy_that_does_not_change_the_store(self):
        self.store.set_goal(T1, "One")
        self.store.plans().clear()
        self.assertEqual(set(self.store.plans()), {T1})

    def test_plans_and_items_are_frozen(self):
        self.store.add_items(T1, ["a"])
        plan = self.store.plan(T1)
        with self.assertRaises(AttributeError):
            plan.goal = "x"
        with self.assertRaises(AttributeError):
            plan.items[0].state = "done"

    def test_updated_at_is_the_time_of_the_last_change_to_that_plan(self):
        with mock.patch.object(plan_module, "_now", return_value=at(1)):
            self.store.set_goal(T1, "Goal")
            self.store.set_goal(T2, "Other")
        self.assertEqual(self.store.plan(T1).updated_at, at(1))
        with mock.patch.object(plan_module, "_now", return_value=at(7)):
            self.store.add_items(T1, ["a"])
        self.assertEqual(self.store.plan(T1).updated_at, at(7))
        self.assertEqual(self.store.plan(T2).updated_at, at(1))

    def test_updated_at_drops_fractions_of_a_second(self):
        now = datetime(2026, 11, 14, 14, 40, 5, 999999, tzinfo=timezone.utc)
        with mock.patch.object(plan_module, "_now", return_value=now):
            self.store.set_goal(T1, "Goal")
        self.assertEqual(self.store.plan(T1).updated_at, at(5))

    def test_every_kind_of_change_moves_updated_at(self):
        steps = [
            lambda: self.store.add_items(T1, ["a"]),
            lambda: self.store.set_goal(T1, "Goal"),
            lambda: self.store.set_status(T1, "Status"),
            lambda: self.store.mark(T1, "1", "doing"),
        ]
        for second, step in enumerate(steps, start=1):
            with mock.patch.object(plan_module, "_now", return_value=at(second)):
                step()
            self.assertEqual(self.store.plan(T1).updated_at, at(second))

    def test_the_real_clock_gives_the_current_utc_time(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        self.store.set_goal(T1, "Goal")
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, self.store.plan(T1).updated_at)
        self.assertLessEqual(self.store.plan(T1).updated_at, after)


class TextRules(StoreTestCase):
    def test_an_empty_or_blank_text_is_rejected(self):
        for text in ["", "   ", "\n\t"]:
            with self.assertRaises(PlanError):
                self.store.set_goal(T1, text)
            with self.assertRaises(PlanError):
                self.store.set_status(T1, text)
            with self.assertRaises(PlanError):
                self.store.add_items(T1, [text])
        self.assertIsNone(self.store.plan(T1))

    def test_a_goal_has_at_most_500_characters(self):
        self.store.set_goal(T1, "g" * 500)
        with self.assertRaisesRegex(PlanError, "500"):
            self.store.set_goal(T1, "g" * 501)
        self.assertEqual(self.store.plan(T1).goal, "g" * 500)

    def test_a_status_line_has_at_most_160_characters(self):
        self.store.set_status(T1, "s" * 160)
        with self.assertRaisesRegex(PlanError, "160"):
            self.store.set_status(T1, "s" * 161)

    def test_an_item_has_at_most_200_characters(self):
        self.store.add_items(T1, ["i" * 200], children=[["c" * 200]])
        with self.assertRaisesRegex(PlanError, "200"):
            self.store.add_items(T1, ["i" * 201])
        with self.assertRaisesRegex(PlanError, "200"):
            self.store.add_items(T1, ["ok"], children=[["c" * 201]])
        with self.assertRaisesRegex(PlanError, "200"):
            self.store.add_items(T1, ["c" * 201], parent="1")

    def test_the_length_limit_applies_after_stripping(self):
        self.store.set_status(T1, "  " + "s" * 160 + "  ")
        self.assertEqual(self.store.plan(T1).status, "s" * 160)

    def test_a_text_that_is_not_a_string_is_rejected(self):
        with self.assertRaises(PlanError):
            self.store.set_goal(T1, 5)
        with self.assertRaises(PlanError):
            self.store.add_items(T1, [None])
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["a"], children=[[3]])

    def test_an_empty_thread_id_is_rejected(self):
        with self.assertRaises(PlanError):
            self.store.set_goal("", "Goal")
        with self.assertRaises(PlanError):
            self.store.set_goal(None, "Goal")

    def test_a_rejected_request_leaves_the_plan_and_the_log_unchanged(self):
        self.store.set_goal(T1, "Goal")
        before = self.log.read_text()
        with self.assertRaises(PlanError):
            self.store.set_goal(T1, "")
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["fine", ""])
        self.assertEqual(self.log.read_text(), before)
        self.assertEqual(self.store.plan(T1).goal, "Goal")
        self.assertEqual(self.store.plan(T1).items, ())


class RejectionMessages(StoreTestCase):
    """The message of a PlanError is shown to the agent, so it names the problem."""

    def assertRejected(self, call, *fragments):
        with self.assertRaises(PlanError) as caught:
            call()
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def test_text_rejections_name_the_text_and_the_problem(self):
        s = self.store
        self.assertRejected(lambda: s.set_goal(T1, " "), "goal is empty")
        self.assertRejected(lambda: s.set_goal(T1, 5), "goal must be a string")
        self.assertRejected(lambda: s.set_goal(T1, "g" * 501), "goal has 501", "limit is 500")
        self.assertRejected(lambda: s.set_status(T1, ""), "status line is empty")
        self.assertRejected(lambda: s.add_items(T1, [""]), "item text is empty")
        self.assertRejected(lambda: s.add_items(T1, ["a"], children=[[""]]), "item text is empty")
        self.assertRejected(lambda: s.set_goal("", "Goal"), "thread id is empty")

    def test_shape_rejections_say_what_to_give(self):
        s = self.store
        s.add_items(T1, ["a"], children=[["a1"]])
        self.assertRejected(lambda: s.add_items(T1, []), "non-empty list of item texts")
        self.assertRejected(lambda: s.add_items(T1, ["b"], children=[]),
                            "one list of child texts per item")
        self.assertRejected(lambda: s.add_items(T1, ["x"], parent="1", children=[["y"]]),
                            "cannot have children", "two levels only")
        self.assertRejected(lambda: s.add_items(T1, ["x"], parent="1.1"),
                            "1.1 is a child item", "give a top-level item as the parent")
        self.assertRejected(lambda: s.add_items(T1, ["x"], parent="4"), "no top-level item 4")
        self.assertRejected(lambda: s.mark(T1, 1, "done"), "item id must be a string")

    def test_state_rejections_say_which_item_and_why(self):
        s = self.store
        s.add_items(T1, ["a", "b"], children=[["a1"], []])
        s.mark(T1, "2", "done")
        self.assertRejected(lambda: s.mark(T1, "2", "todo"), "\"doing\" or \"done\"", "'todo'")
        self.assertRejected(lambda: s.mark(T1, "2", "doing"), "2 is done", "never moves back")
        self.assertRejected(lambda: s.mark(T1, "1", "done"), "1 has children",
                            "mark the children")
        self.assertRejected(lambda: s.mark(T1, "9", "done"), "no item 9")
        self.assertRejected(lambda: s.mark(T1, "1.5", "done"), "no item 1.5")
        self.assertRejected(lambda: s.add_items(T1, ["x"], parent="2"),
                            "2 is done", "Add a new top-level item instead")


class AddingItems(StoreTestCase):
    def test_top_level_items_are_numbered_from_one(self):
        ids = self.store.add_items(T1, ["a", "b", "c"])
        self.assertEqual(ids, ["1", "2", "3"])
        self.assertEqual(self.states(), [("1", "todo"), ("2", "todo"), ("3", "todo")])
        self.assertEqual([i.text for i in self.store.plan(T1).items], ["a", "b", "c"])

    def test_new_top_level_items_continue_the_numbering(self):
        self.store.add_items(T1, ["a", "b"])
        self.assertEqual(self.store.add_items(T1, ["c"]), ["3"])

    def test_item_texts_are_stripped(self):
        self.store.add_items(T1, ["  a "], children=[[" a1  "]])
        item = self.store.plan(T1).items[0]
        self.assertEqual(item.text, "a")
        self.assertEqual(item.children[0].text, "a1")

    def test_children_given_with_new_items_are_numbered_under_each_item(self):
        ids = self.store.add_items(T1, ["a", "b", "c"], children=[["a1", "a2"], [], ["c1"]])
        self.assertEqual(ids, ["1", "1.1", "1.2", "2", "3", "3.1"])
        plan = self.store.plan(T1)
        self.assertEqual([c.text for c in plan.items[0].children], ["a1", "a2"])
        self.assertEqual(plan.items[1].children, ())
        self.assertEqual(plan.items[2].children[0], Item("3.1", "c1", "todo", (), mock.ANY))

    def test_children_given_with_a_parent_are_appended_to_that_item(self):
        self.store.add_items(T1, ["a", "b"], children=[["a1"], []])
        self.assertEqual(self.store.add_items(T1, ["a2", "a3"], parent="1"), ["1.2", "1.3"])
        self.assertEqual(self.store.add_items(T1, ["b1"], parent="2"), ["2.1"])
        first = self.store.plan(T1).items[0]
        self.assertEqual(first.text, "a")
        self.assertEqual(first.children[2], Item("1.3", "a3", "todo", (), mock.ANY))
        self.assertEqual(
            self.states(),
            [("1", "todo"), ("1.1", "todo"), ("1.2", "todo"), ("1.3", "todo"),
             ("2", "todo"), ("2.1", "todo")],
        )

    def test_the_parent_id_may_have_surrounding_spaces(self):
        self.store.add_items(T1, ["a"])
        self.assertEqual(self.store.add_items(T1, ["a1"], parent=" 1 "), ["1.1"])

    def test_a_parent_together_with_children_is_rejected(self):
        self.store.add_items(T1, ["a"])
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["a1"], parent="1", children=[["deeper"]])

    def test_an_unknown_parent_is_rejected(self):
        with self.assertRaisesRegex(PlanError, "7"):
            self.store.add_items(T1, ["x"], parent="7")
        self.store.add_items(T1, ["a"])
        with self.assertRaisesRegex(PlanError, "7"):
            self.store.add_items(T1, ["x"], parent="7")

    def test_a_child_item_cannot_be_a_parent(self):
        self.store.add_items(T1, ["a"], children=[["a1"]])
        with self.assertRaisesRegex(PlanError, "two levels"):
            self.store.add_items(T1, ["deeper"], parent="1.1")

    def test_a_done_item_gets_no_new_children_and_the_error_suggests_a_new_top_level_item(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "done")
        with self.assertRaisesRegex(PlanError, "top-level"):
            self.store.add_items(T1, ["late"], parent="1")
        self.assertEqual(self.states(), [("1", "done")])

    def test_an_item_whose_children_are_all_done_gets_no_new_children(self):
        self.store.add_items(T1, ["a"], children=[["a1"]])
        self.store.mark(T1, "1.1", "done")
        with self.assertRaisesRegex(PlanError, "top-level"):
            self.store.add_items(T1, ["late"], parent="1")

    def test_an_empty_list_of_items_is_rejected(self):
        with self.assertRaises(PlanError):
            self.store.add_items(T1, [])

    def test_texts_must_be_a_list(self):
        with self.assertRaises(PlanError):
            self.store.add_items(T1, "abc")

    def test_children_must_match_the_items_one_to_one(self):
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["a", "b"], children=[["a1"]])
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["a"], children=[["a1"], ["b1"]])
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["a"], children=["a1"])
        with self.assertRaises(PlanError):
            self.store.add_items(T1, ["a"], children="a1")

    def test_adding_items_keeps_the_goal_and_status(self):
        self.store.set_goal(T1, "Goal")
        self.store.set_status(T1, "Status")
        self.store.add_items(T1, ["a"])
        self.assertEqual(self.store.plan(T1).goal, "Goal")
        self.assertEqual(self.store.plan(T1).status, "Status")


class MarkingItems(StoreTestCase):
    def test_a_leaf_moves_from_todo_to_doing_to_done(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "doing")
        self.assertEqual(self.states(), [("1", "doing")])
        self.store.mark(T1, "1", "done")
        self.assertEqual(self.states(), [("1", "done")])

    def test_a_leaf_may_jump_from_todo_to_done(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "done")
        self.assertEqual(self.states(), [("1", "done")])

    def test_a_leaf_never_moves_back(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "done")
        with self.assertRaises(PlanError):
            self.store.mark(T1, "1", "doing")
        self.assertEqual(self.states(), [("1", "done")])

    def test_only_doing_and_done_can_be_requested(self):
        self.store.add_items(T1, ["a"])
        for state in ["todo", "DONE", "finished", ""]:
            with self.assertRaises(PlanError):
                self.store.mark(T1, "1", state)

    def test_a_request_for_the_current_state_does_nothing(self):
        with mock.patch.object(plan_module, "_now", return_value=at(1)):
            self.store.add_items(T1, ["a", "b"])
            self.store.mark(T1, "1", "doing")
            self.store.mark(T1, "2", "done")
        before = self.log.read_text()
        with mock.patch.object(plan_module, "_now", return_value=at(9)):
            self.store.mark(T1, "1", "doing")
            self.store.mark(T1, "2", "done")
        self.assertEqual(self.log.read_text(), before)
        self.assertEqual(self.store.plan(T1).updated_at, at(1))
        self.assertEqual(self.states(), [("1", "doing"), ("2", "done")])

    def test_a_request_for_the_current_state_of_a_child_does_nothing(self):
        self.store.add_items(T1, ["a"], children=[["a1", "a2"]])
        self.store.mark(T1, "1.1", "doing")
        before = self.log.read_text()
        self.store.mark(T1, "1.1", "doing")
        self.assertEqual(self.log.read_text(), before)

    def test_an_item_id_that_is_not_a_string_is_rejected(self):
        self.store.add_items(T1, ["a"])
        with self.assertRaisesRegex(PlanError, "string"):
            self.store.mark(T1, 1, "done")
        with self.assertRaisesRegex(PlanError, "string"):
            self.store.add_items(T1, ["a1"], parent=1)

    def test_a_child_item_can_be_marked(self):
        self.store.add_items(T1, ["a"], children=[["a1", "a2"]])
        self.store.mark(T1, "1.2", "doing")
        self.assertEqual(self.states(), [("1", "doing"), ("1.1", "todo"), ("1.2", "doing")])

    def test_an_item_with_children_cannot_be_marked(self):
        self.store.add_items(T1, ["a"], children=[["a1"]])
        with self.assertRaisesRegex(PlanError, "children"):
            self.store.mark(T1, "1", "done")

    def test_an_unknown_item_or_thread_is_rejected(self):
        with self.assertRaises(PlanError):
            self.store.mark(T1, "1", "done")
        self.store.add_items(T1, ["a"], children=[["a1"]])
        for item_id in ["2", "1.2", "0", "1.1.1", "", "x", "1.", ".1"]:
            with self.assertRaises(PlanError, msg=item_id):
                self.store.mark(T1, item_id, "done")
        with self.assertRaises(PlanError):
            self.store.mark(T2, "1", "done")

    def test_the_item_id_may_have_surrounding_spaces(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, " 1 ", "done")
        self.assertEqual(self.states(), [("1", "done")])

    def test_marking_keeps_text_and_other_items(self):
        self.store.add_items(T1, ["a", "b"], children=[["a1"], []])
        self.store.mark(T1, "1.1", "done")
        plan = self.store.plan(T1)
        self.assertEqual(plan.items[0].text, "a")
        self.assertEqual(plan.items[0].children[0].text, "a1")
        self.assertEqual(plan.items[1], Item("2", "b", "todo", (), mock.ANY))


class DerivedParentState(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.add_items(T1, ["parent"], children=[["c1", "c2", "c3"]])

    def parent_state(self):
        return self.store.plan(T1).items[0].state

    def test_a_parent_whose_children_are_all_todo_is_todo(self):
        self.assertEqual(self.parent_state(), "todo")

    def test_a_parent_with_one_child_doing_is_doing(self):
        self.store.mark(T1, "1.2", "doing")
        self.assertEqual(self.parent_state(), "doing")

    def test_a_parent_with_some_children_done_is_doing(self):
        self.store.mark(T1, "1.1", "done")
        self.store.mark(T1, "1.3", "done")
        self.assertEqual(self.parent_state(), "doing")

    def test_a_parent_whose_children_are_all_done_is_done(self):
        for child in ["1.1", "1.2", "1.3"]:
            self.store.mark(T1, child, "done")
        self.assertEqual(self.parent_state(), "done")

    def test_a_new_child_turns_a_doing_parent_back_to_its_derived_state(self):
        self.store.mark(T1, "1.1", "done")
        self.store.mark(T1, "1.2", "done")
        self.store.add_items(T1, ["c4"], parent="1")
        self.assertEqual(self.parent_state(), "doing")

    def test_children_given_to_a_doing_leaf_decide_its_state(self):
        self.store.add_items(T1, ["leaf"])
        self.store.mark(T1, "2", "doing")
        self.store.add_items(T1, ["l1"], parent="2")
        self.assertEqual(self.store.plan(T1).items[1].state, "todo")
        self.store.mark(T1, "2.1", "done")
        self.assertEqual(self.store.plan(T1).items[1].state, "done")


class TheLog(StoreTestCase):
    def test_each_change_appends_one_json_line_in_the_documented_shape(self):
        with mock.patch.object(plan_module, "_now", return_value=at(0)):
            self.store.set_goal(T1, " Goal ")
            self.store.set_status(T1, "Status")
            self.store.add_items(T1, ["a", "b"], children=[["a1"], []])
            self.store.add_items(T1, ["b1"], parent="2")
            self.store.mark(T1, "2.1", "done")
        ts = "2026-11-14T14:40:00Z"
        self.assertEqual(self.log_records(), [
            {"ts": ts, "thread": T1, "op": "goal", "text": "Goal"},
            {"ts": ts, "thread": T1, "op": "status", "text": "Status"},
            {"ts": ts, "thread": T1, "op": "add", "parent": None,
             "texts": ["a", "b"], "children": [["a1"], []]},
            {"ts": ts, "thread": T1, "op": "add", "parent": "2",
             "texts": ["b1"], "children": None},
            {"ts": ts, "thread": T1, "op": "mark", "id": "2.1", "state": "done"},
        ])

    def test_the_log_directory_is_created_on_the_first_change(self):
        self.assertFalse(self.log.parent.exists())
        self.store.set_goal(T1, "Goal")
        self.assertTrue(self.log.exists())

    def test_all_missing_directories_of_the_log_are_created(self):
        nested = PlanStore(self.root / "a" / "b" / "plan.jsonl")
        nested.set_goal(T1, "Goal")
        self.assertTrue((self.root / "a" / "b" / "plan.jsonl").exists())

    def test_reading_a_store_creates_no_log(self):
        self.store.plans()
        self.store.plan(T1)
        self.assertFalse(self.log.parent.exists())

    def test_each_line_reaches_the_file_before_the_method_returns(self):
        self.store.set_goal(T1, "Goal")
        self.assertEqual(len(self.log_records()), 1)
        self.store.set_goal(T1, "Goal 2")
        self.assertEqual(len(self.log_records()), 2)

    def test_non_ascii_text_survives_the_log(self):
        goal = "Prüfe Lichter ✓ \u2028 line separator"
        self.store.set_goal(T1, goal)
        self.assertEqual(self.reopen().plan(T1).goal, goal)
        self.assertIn("Prüfe Lichter ✓", self.log.read_text(encoding="utf-8"))

    def test_a_new_store_replays_the_log_to_the_same_plans(self):
        with mock.patch.object(plan_module, "_now", return_value=at(3)):
            self.store.set_goal(T1, "Goal")
            self.store.set_status(T2, "Status")
            self.store.add_items(T1, ["a", "b"], children=[["a1", "a2"], []])
            self.store.add_items(T1, ["b1"], parent="2")
            self.store.mark(T1, "1.1", "done")
            self.store.mark(T1, "2.1", "doing")
        self.assertEqual(self.reopen().plans(), self.store.plans())

    def test_a_reopened_store_continues_the_log(self):
        self.store.add_items(T1, ["a"])
        again = self.reopen()
        self.assertEqual(again.add_items(T1, ["b"]), ["2"])
        self.assertEqual(len(self.log_records()), 2)
        self.assertEqual(self.reopen().plans(), again.plans())

    def test_a_line_that_does_not_parse_is_skipped_with_a_warning(self):
        self.store.set_goal(T1, "Goal")
        with self.log.open("a") as f:
            f.write("{not json\n")
            f.write("\n")
        self.store.set_status(T1, "Status")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.reopen()
        self.assertEqual(again.plan(T1).goal, "Goal")
        self.assertEqual(again.plan(T1).status, "Status")
        self.assertIn("plan.jsonl:2", err.getvalue())
        self.assertNotIn(":3", err.getvalue())

    def test_a_line_with_a_broken_record_is_skipped_with_a_warning(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "done")
        bad = [
            [1, 2],
            {"ts": "2026-11-14T14:40:00Z", "thread": T1, "op": "mark", "id": "1", "state": "doing"},
            {"ts": "2026-11-14T14:40:00Z", "thread": T1, "op": "explode"},
            {"ts": "2026-11-14T14:40:00Z", "thread": T1, "op": "goal"},
            {"ts": "yesterday", "thread": T1, "op": "goal", "text": "x"},
            {"ts": "2026-11-14T14:40:00Z", "op": "goal", "text": "x"},
            {"ts": "2026-11-14T14:40:00Z", "thread": T1, "op": "add", "texts": ["c"]},
            {"ts": "2026-11-14T14:40:00Z", "thread": T1, "op": "goal", "text": "  "},
        ]
        with self.log.open("a") as f:
            for record in bad:
                f.write(json.dumps(record) + "\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.reopen()
        self.assertEqual(again.plans(), self.store.plans())
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), len(bad))
        for n, line in enumerate(lines, start=3):
            self.assertIn(f"plan.jsonl:{n}:", line)
        self.assertIn("JSON object", lines[0])
        self.assertIn("never moves back", lines[1])
        self.assertIn("Unknown operation 'explode'", lines[2])

    def test_a_torn_last_line_does_not_swallow_the_next_change(self):
        self.store.set_goal(T1, "Goal")
        with self.log.open("a") as f:
            f.write('{"ts": "2026-11-14T14:4')
        with contextlib.redirect_stderr(io.StringIO()):
            again = self.reopen()
            again.set_status(T1, "Status")
            third = self.reopen()
        self.assertEqual(third.plan(T1).status, "Status")
        self.assertEqual(third.plan(T1).goal, "Goal")

    def test_a_torn_single_character_log_does_not_swallow_the_next_change(self):
        self.log.parent.mkdir()
        self.log.write_text("{")
        with contextlib.redirect_stderr(io.StringIO()):
            PlanStore(self.log).set_goal(T1, "Goal")
            again = self.reopen()
        self.assertEqual(again.plan(T1).goal, "Goal")

    def test_a_failed_write_leaves_memory_unchanged(self):
        self.log.parent.write_text("a file where the log directory must be")
        with self.assertRaises(OSError):
            self.store.set_goal(T1, "Goal")
        self.assertIsNone(self.store.plan(T1))


class Invariants(StoreTestCase):
    def fill(self):
        self.store.set_goal(T1, "Goal")
        self.store.add_items(T1, ["a", "b", "c"], children=[["a1", "a2"], [], ["c1"]])
        self.store.add_items(T1, ["b1"], parent="2")
        self.store.mark(T1, "1.1", "done")
        self.store.mark(T1, "2.1", "doing")
        self.store.mark(T1, "3.1", "done")
        self.store.set_status(T2, "Other")

    def tamper(self, thread, **changes):
        """Replace fields of one plan in memory, behind the store's back."""
        old = self.store._plans[thread]
        self.store._plans[thread] = Plan(**{**old.__dict__, **changes})

    def test_a_store_built_through_its_methods_keeps_all_invariants(self):
        self.fill()
        self.store.check_invariants()
        self.reopen().check_invariants()

    def test_checking_invariants_prints_no_warning_for_a_skipped_log_line(self):
        self.store.set_goal(T1, "Goal")
        with self.log.open("a") as f:
            f.write("{not json\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.store.check_invariants()
        self.assertEqual(err.getvalue(), "")

    def test_an_empty_store_keeps_all_invariants(self):
        self.store.check_invariants()

    def test_a_gap_in_top_level_ids_breaks_invariant_1(self):
        self.fill()
        items = self.store.plan(T1).items
        self.tamper(T1, items=(items[0], items[2]))
        with self.assertRaisesRegex(AssertionError, "top-level ids"):
            self.store.check_invariants()

    def test_a_gap_in_child_ids_breaks_invariant_1(self):
        self.fill()
        first = self.store.plan(T1).items[0]
        broken = Item("1", "a", "todo", (Item("1.2", "a2", "todo", (), at(0)),), at(0))
        self.tamper(T1, items=(broken,) + self.store.plan(T1).items[1:])
        with self.assertRaisesRegex(AssertionError, "child ids of item 1 "):
            self.store.check_invariants()
        self.assertEqual(first.id, "1")

    def test_a_third_level_breaks_invariant_2(self):
        self.fill()
        deep = Item("1.1", "a1", "todo", (Item("1.1.1", "x", "todo", (), at(0)),), at(0))
        broken = Item("1", "a", "todo", (deep,), at(0))
        self.tamper(T1, items=(broken,) + self.store.plan(T1).items[1:])
        with self.assertRaisesRegex(AssertionError, "1.1 has children: deeper than two"):
            self.store.check_invariants()

    def test_an_unknown_leaf_state_breaks_invariant_3(self):
        self.fill()
        broken = Item("2", "b", "todo", (Item("2.1", "b1", "busy", (), at(0)),), at(0))
        items = self.store.plan(T1).items
        self.tamper(T1, items=(items[0], broken, items[2]))
        with self.assertRaisesRegex(AssertionError, "2.1 has leaf state 'busy'"):
            self.store.check_invariants()

    def test_an_unknown_state_on_a_top_level_leaf_breaks_invariant_3(self):
        self.fill()
        items = self.store.plan(T1).items
        self.tamper(T1, items=(items[0], items[1], items[2], Item("4", "d", "later", (), at(0))))
        with self.assertRaisesRegex(AssertionError, "item 4 has leaf state 'later'"):
            self.store.check_invariants()

    def test_a_parent_state_that_differs_from_its_children_breaks_invariant_4(self):
        self.fill()
        items = self.store.plan(T1).items
        broken = Item("3", "c", "doing", items[2].children, at(0))
        self.tamper(T1, items=(items[0], items[1], broken))
        with self.assertRaisesRegex(AssertionError, "item 3 has state 'doing', but its derived"):
            self.store.check_invariants()

    def test_memory_that_differs_from_the_log_breaks_invariant_5(self):
        self.fill()
        self.tamper(T1, goal="Changed behind the log")
        with self.assertRaisesRegex(
                AssertionError, "^a fresh replay of the log differs from the memory$"):
            self.store.check_invariants()

    def test_invariants_are_checked_even_when_assertions_are_compiled_out(self):
        import subprocess
        import sys
        code = (
            "import pathlib, sys\n"
            "from agentmap.plan import PlanStore, Plan\n"
            "s = PlanStore(pathlib.Path(sys.argv[1]))\n"
            "s.set_goal('t', 'g')\n"
            "s._plans['t'] = Plan('other', None, (), s._plans['t'].updated_at, None)\n"
            "try:\n"
            "    s.check_invariants()\n"
            "except AssertionError:\n"
            "    print('caught')\n"
        )
        repo = pathlib.Path(__file__).resolve().parent.parent
        out = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.root / "o.jsonl")],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        self.assertEqual(out.stdout.strip(), "caught")


class ChangeTimes(StoreTestCase):
    """An item's changed_at is the log time of its creation or its latest state change."""

    def step(self, second, call, *args, **kwargs):
        with mock.patch.object(plan_module, "_now", return_value=at(second)):
            return call(*args, **kwargs)

    def item(self, item_id, thread=T1):
        top = next(i for i in self.store.plan(thread).items if i.id == item_id.split(".")[0])
        return top if "." not in item_id else next(k for k in top.children if k.id == item_id)

    def test_a_new_item_changed_at_is_the_time_it_was_added(self):
        self.step(1, self.store.add_items, T1, ["a"], children=[["a1"]])
        self.step(2, self.store.add_items, T1, ["b"])
        self.assertEqual(self.item("1.1").changed_at, at(1))
        self.assertEqual(self.item("2").changed_at, at(2))

    def test_marking_a_leaf_moves_its_changed_at(self):
        self.step(1, self.store.add_items, T1, ["a"])
        self.step(5, self.store.mark, T1, "1", "doing")
        self.assertEqual(self.item("1").changed_at, at(5))
        self.step(9, self.store.mark, T1, "1", "done")
        self.assertEqual(self.item("1").changed_at, at(9))

    def test_a_request_for_the_current_state_keeps_changed_at(self):
        self.step(1, self.store.add_items, T1, ["a"])
        self.step(5, self.store.mark, T1, "1", "doing")
        self.step(9, self.store.mark, T1, "1", "doing")
        self.assertEqual(self.item("1").changed_at, at(5))

    def test_a_change_to_one_item_keeps_the_changed_at_of_the_others(self):
        self.step(1, self.store.add_items, T1, ["a", "b"], children=[["a1", "a2"], []])
        self.step(5, self.store.mark, T1, "1.1", "done")
        self.step(6, self.store.set_goal, T1, "Goal")
        self.assertEqual(self.item("1.2").changed_at, at(1))
        self.assertEqual(self.item("2").changed_at, at(1))

    def test_a_parent_changed_at_is_the_latest_change_of_itself_or_its_children(self):
        self.step(1, self.store.add_items, T1, ["a"], children=[["a1", "a2"]])
        self.step(5, self.store.mark, T1, "1.2", "doing")
        self.assertEqual(self.item("1").changed_at, at(5))
        self.assertEqual(self.item("1.1").changed_at, at(1))

    def test_adding_children_moves_the_parent_changed_at(self):
        self.step(1, self.store.add_items, T1, ["a"])
        self.step(7, self.store.add_items, T1, ["a1"], parent="1")
        self.assertEqual(self.item("1").changed_at, at(7))
        self.assertEqual(self.item("1.1").changed_at, at(7))

    def test_a_child_change_logged_with_an_earlier_clock_does_not_move_its_parent_back(self):
        self.step(1, self.store.add_items, T1, ["a"], children=[["a1", "a2"]])
        self.step(9, self.store.mark, T1, "1.1", "doing")
        self.step(4, self.store.mark, T1, "1.2", "doing")
        self.assertEqual(self.item("1.2").changed_at, at(4))
        self.assertEqual(self.item("1").changed_at, at(9))

    def test_a_replay_gives_the_same_changed_at(self):
        self.step(1, self.store.add_items, T1, ["a", "b"], children=[["a1", "a2"], []])
        self.step(3, self.store.add_items, T1, ["b1"], parent="2")
        self.step(5, self.store.mark, T1, "1.2", "done")
        self.step(7, self.store.mark, T1, "2.1", "doing")
        self.assertEqual(self.reopen().plans(), self.store.plans())
        self.assertEqual(self.reopen().plan(T1).items[1].changed_at, at(7))


class StatusTime(StoreTestCase):
    def test_a_plan_without_a_status_has_no_status_time(self):
        self.store.set_goal(T1, "Goal")
        self.assertIsNone(self.store.plan(T1).status_at)

    def test_status_at_is_the_time_of_the_latest_status(self):
        with mock.patch.object(plan_module, "_now", return_value=at(2)):
            self.store.set_status(T1, "One")
        with mock.patch.object(plan_module, "_now", return_value=at(6)):
            self.store.set_status(T1, "Two")
        self.assertEqual(self.store.plan(T1).status_at, at(6))

    def test_other_changes_keep_status_at(self):
        with mock.patch.object(plan_module, "_now", return_value=at(2)):
            self.store.set_status(T1, "Status")
        with mock.patch.object(plan_module, "_now", return_value=at(8)):
            self.store.set_goal(T1, "Goal")
            self.store.add_items(T1, ["a"])
            self.store.mark(T1, "1", "done")
        self.assertEqual(self.store.plan(T1).status_at, at(2))

    def test_a_replay_gives_the_same_status_at(self):
        with mock.patch.object(plan_module, "_now", return_value=at(4)):
            self.store.set_status(T1, "Status")
        self.assertEqual(self.reopen().plan(T1).status_at, at(4))


class PlanEvents(StoreTestCase):
    """events() lists what agents posted, newest first."""

    def test_a_new_store_has_no_events(self):
        self.assertEqual(self.store.events(), [])

    def test_each_kind_of_change_gives_its_event_with_its_text_time_and_thread(self):
        with mock.patch.object(plan_module, "_now", return_value=at(1)):
            self.store.set_goal(T1, " Cap lights ")
        with mock.patch.object(plan_module, "_now", return_value=at(2)):
            self.store.set_status(T1, "Profiling")
        with mock.patch.object(plan_module, "_now", return_value=at(3)):
            self.store.add_items(T1, ["Count"])
        with mock.patch.object(plan_module, "_now", return_value=at(4)):
            self.store.mark(T1, "1", "doing")
        with mock.patch.object(plan_module, "_now", return_value=at(5)):
            self.store.mark(T1, "1", "done")
        self.assertEqual(self.store.events(), [
            Event(at(5), T1, "done", "Count"),
            Event(at(4), T1, "doing", "Count"),
            Event(at(3), T1, "added", "Count"),
            Event(at(2), T1, "status", "Profiling"),
            Event(at(1), T1, "goal", "Cap lights"),
        ])

    def test_adding_items_gives_one_added_event_per_item_children_included(self):
        with mock.patch.object(plan_module, "_now", return_value=at(1)):
            self.store.add_items(T1, ["a", "b"], children=[["a1", "a2"], []])
        self.assertEqual([e.text for e in self.store.events()], ["b", "a2", "a1", "a"])
        self.assertEqual({e.kind for e in self.store.events()}, {"added"})

    def test_adding_children_under_a_parent_gives_one_added_event_per_child(self):
        self.store.add_items(T1, ["a"])
        self.store.add_items(T1, ["a1", "a2"], parent="1")
        self.assertEqual([e.text for e in self.store.events()][:2], ["a2", "a1"])

    def test_marking_a_child_gives_one_event_with_the_child_text_only(self):
        self.store.add_items(T1, ["parent"], children=[["only child"]])
        self.store.mark(T1, "1.1", "done")
        self.assertEqual(self.store.events()[0], Event(mock.ANY, T1, "done", "only child"))
        self.assertEqual(len(self.store.events()), 3)

    def test_a_request_that_changes_nothing_gives_no_event(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "doing")
        self.store.mark(T1, "1", "doing")
        self.assertEqual([e.kind for e in self.store.events()], ["doing", "added"])

    def test_a_rejected_request_gives_no_event(self):
        self.store.add_items(T1, ["a"])
        with self.assertRaises(PlanError):
            self.store.mark(T1, "7", "done")
        self.assertEqual(len(self.store.events()), 1)

    def test_events_of_all_threads_are_merged_newest_first(self):
        with mock.patch.object(plan_module, "_now", return_value=at(1)):
            self.store.set_goal(T1, "One")
        with mock.patch.object(plan_module, "_now", return_value=at(2)):
            self.store.set_goal(T2, "Two")
        with mock.patch.object(plan_module, "_now", return_value=at(3)):
            self.store.set_status(T1, "Three")
        self.assertEqual([(e.thread_id, e.text) for e in self.store.events()],
                         [(T1, "Three"), (T2, "Two"), (T1, "One")])

    def test_at_most_200_events_are_kept_newest_first(self):
        self.store.add_items(T1, [f"item {n}" for n in range(150)])
        self.store.add_items(T2, [f"other {n}" for n in range(60)])
        events = self.store.events()
        self.assertEqual(len(events), 200)
        self.assertEqual(events[0].text, "other 59")
        self.assertEqual(events[-1].text, "item 10")

    def test_a_reopened_store_replays_the_same_events(self):
        self.store.set_goal(T1, "Goal")
        self.store.add_items(T1, ["a"], children=[["a1"]])
        self.store.mark(T1, "1.1", "doing")
        self.store.set_status(T2, "Other")
        self.assertEqual(self.reopen().events(), self.store.events())

    def test_a_logged_line_that_changes_nothing_gives_no_event_on_replay(self):
        self.store.add_items(T1, ["a"])
        self.store.mark(T1, "1", "doing")
        repeat = self.log.read_text().splitlines()[-1]
        with self.log.open("a") as f:
            f.write(repeat + "\n")
        self.assertEqual([e.kind for e in self.reopen().events()], ["doing", "added"])

    def test_events_returns_a_copy_that_does_not_change_the_store(self):
        self.store.set_goal(T1, "Goal")
        self.store.events().clear()
        self.assertEqual(len(self.store.events()), 1)

    def test_events_that_differ_from_the_log_break_invariant_6(self):
        self.store.set_goal(T1, "Goal")
        self.store._events.append(Event(at(0), T1, "goal", "Not in the log"))
        with self.assertRaisesRegex(
                AssertionError, "^the events of a fresh replay of the log differ from the memory$"):
            self.store.check_invariants()


class Concurrency(StoreTestCase):
    def test_concurrent_requests_give_gap_free_ids_and_a_log_that_replays(self):
        results = []

        def worker():
            for _ in range(20):
                results.extend(self.store.add_items(T1, ["x"]))

        workers = [threading.Thread(target=worker) for _ in range(8)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        self.assertEqual(sorted(results, key=int), [str(n) for n in range(1, 161)])
        self.store.check_invariants()


if __name__ == "__main__":
    unittest.main()
