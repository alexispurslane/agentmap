"""The status store: the one-line status that each agent thread posts through MCP."""

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agentmap import status as status_module
from agentmap.events import Event
from agentmap.status import STATUS_MAX, Status, StatusError, StatusStore

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 14, 15, 5, tzinfo=timezone.utc)


class StatusStoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / ".agentmap" / "status.jsonl"
        self.clock = mock.patch.object(status_module, "_now", return_value=T0)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def at(self, when):
        status_module._now.return_value = when

    def test_a_posted_status_is_the_threads_status_with_its_time(self):
        store = StatusStore(self.path)
        store.set_status("t", "  Merging the cull pass.  ")
        self.assertEqual(store.status("t"), Status("Merging the cull pass.", T0))
        self.assertIsNone(store.status("other"))

    def test_a_newer_status_replaces_the_older_one(self):
        store = StatusStore(self.path)
        store.set_status("t", "First.")
        self.at(T1)
        store.set_status("t", "Second.")
        self.assertEqual(store.statuses(), {"t": Status("Second.", T1)})

    def test_statuses_survive_a_restart(self):
        StatusStore(self.path).set_status("t", "Kept.")
        store = StatusStore(self.path)
        self.assertEqual(store.status("t"), Status("Kept.", T0))
        store.check_invariants()

    def test_each_new_status_text_is_an_event_newest_first(self):
        store = StatusStore(self.path)
        store.set_status("a", "One.")
        self.at(T1)
        store.set_status("b", "Two.")
        self.assertEqual(store.events(), [Event(T1, "b", "status", "Two."), Event(T0, "a", "status", "One.")])

    def test_posting_the_same_text_again_renews_its_time_but_is_no_new_event(self):
        store = StatusStore(self.path)
        store.set_status("t", "Same.")
        self.at(T1)
        store.set_status("t", "Same.")
        self.assertEqual(store.status("t"), Status("Same.", T1))
        self.assertEqual(len(store.events()), 1)
        store.check_invariants()

    def test_the_event_list_keeps_the_newest_two_hundred(self):
        store = StatusStore(self.path)
        for i in range(205):
            store.set_status("t", f"Step {i}.")
        events = store.events()
        self.assertEqual(len(events), 200)
        self.assertEqual(events[0].text, "Step 204.")

    def test_an_empty_too_long_or_non_text_status_is_refused(self):
        store = StatusStore(self.path)
        for bad in ("", "   ", "x" * (STATUS_MAX + 1), 42):
            with self.subTest(bad=bad), self.assertRaises(StatusError):
                store.set_status("t", bad)
        store.set_status("t", "x" * STATUS_MAX)
        with self.assertRaises(StatusError):
            store.set_status("", "No thread.")

    def test_a_refused_status_writes_nothing(self):
        store = StatusStore(self.path)
        with self.assertRaises(StatusError):
            store.set_status("t", "")
        self.assertFalse(self.path.exists())

    def test_the_log_has_one_json_record_per_line(self):
        store = StatusStore(self.path)
        store.set_status("t", "Één.")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l) for l in lines],
                         [{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "status", "text": "Één."}])

    def test_bad_lines_and_other_records_in_the_log_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            "not json\n[1]\n"
            '{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "goal", "text": "Old plan goal."}\n'
            '{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "status", "text": "Good."}\n'
            '{"ts": "bad", "thread": "t", "op": "status", "text": "Bad time."}\n')
        with mock.patch("sys.stderr"):
            store = StatusStore(self.path)
        self.assertEqual(store.statuses(), {"t": Status("Good.", T0)})

    def test_a_torn_last_line_does_not_swallow_the_next_record(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "sta')
        with mock.patch("sys.stderr"):
            store = StatusStore(self.path)
            store.set_status("t", "After the tear.")
            self.assertEqual(StatusStore(self.path).status("t"), Status("After the tear.", T0))

    def test_each_refusal_says_what_is_wrong(self):
        store = StatusStore(self.path)
        for thread, text, message in [("", "x", "The thread id is empty."),
                                      ("t", 42, "The status line must be a string."),
                                      ("t", "  ", "The status line is empty."),
                                      ("t", "x" * 161, "The status line has 161 characters; the limit is 160.")]:
            with self.subTest(message=message), self.assertRaises(StatusError) as caught:
                store.set_status(thread, text)
            self.assertEqual(str(caught.exception), message)

    def test_a_skipped_log_line_is_reported_with_its_file_and_line_number(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "status", "text": "A."}\n[1]\n')
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            StatusStore(self.path)
        self.assertEqual(err.getvalue(),
                         f"agentmap: {self.path}:2: skipped log line: StatusError('not a JSON object')\n")

    def test_check_invariants_reports_nothing_on_stderr(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json\n")
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            store = StatusStore(self.path)
            err.truncate(0)
            store.check_invariants()
        self.assertEqual(err.getvalue(), "")

    def test_a_blank_line_inside_the_log_is_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('\n\n{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "status", "text": "After."}\n')
        self.assertEqual(StatusStore(self.path).status("t"), Status("After.", T0))

    def test_the_log_folder_is_made_with_its_parents(self):
        deep = self.path.parent / "a" / "b" / "status.jsonl"
        StatusStore(deep).set_status("t", "Deep.")
        self.assertTrue(deep.exists())

    def test_records_are_appended_as_utf8_lines_without_blank_lines(self):
        store = StatusStore(self.path)
        store.set_status("t", "Één.")
        store.set_status("t", "Twee.")
        self.assertEqual(self.path.read_bytes(), (
            '{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "status", "text": "Één."}\n'
            '{"ts": "2026-11-14T15:00:00Z", "thread": "t", "op": "status", "text": "Twee."}\n').encode())

    def test_a_one_byte_torn_log_gets_a_line_break_before_the_next_record(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"x")
        with mock.patch("sys.stderr"):
            StatusStore(self.path).set_status("t", "Next.")
        self.assertTrue(self.path.read_bytes().startswith(b'x\n{"ts"'))

    def test_without_a_fixed_clock_times_are_utc(self):
        self.clock.stop()
        try:
            store = StatusStore(self.path)
            store.set_status("t", "Now.")
            at = store.status("t").at
            self.assertEqual(at.utcoffset().total_seconds(), 0)
            self.assertLess(abs((datetime.now(timezone.utc) - at).total_seconds()), 5)
        finally:
            self.clock.start()

    def test_check_invariants_finds_a_memory_that_differs_from_the_log(self):
        store = StatusStore(self.path)
        store.set_status("t", "Logged.")
        self.path.write_text("")
        with self.assertRaisesRegex(AssertionError, "^a fresh replay of the log differs from the memory; "
                                                    "the events of a fresh replay of the log differ from the memory$"):
            store.check_invariants()


if __name__ == "__main__":
    unittest.main()
