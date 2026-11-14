"""Tests of agentmap.tickets: the ticket parser, the ticket store and the groups.

The store tests use real temporary folders. Each test sets the modification
times of files and folders itself with os.utime, so that "the time moved" is
under the test's control.
"""

import os
import pathlib
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from agentmap.tickets import Group, Note, Ticket, TicketStore, blocked_by, groups, parse_ticket

T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

TK_FILE = """\
---
id: ter-69s8
status: in_progress
deps: [ter-9nrc, ter-aaaa]
links: []
created: 2026-11-04T13:42:25Z
type: feature
priority: 1
assignee: Alexis Purslane
parent: ter-89xq
tags: [rendering, performance, lights]
---
# Per-tile light budget: a title

Body text.

## Design

More body.

## Notes

**2026-11-04T14:17:19Z**

First note.

**2026-11-05T09:00:00Z**

Second note,
two lines.
"""


def text(id_="t-1", *, title="A title", body="", notes="", **fields):
    """The text of a ticket file with these front matter fields."""
    front = {"id": id_, "status": "open", "deps": "[]", "links": "[]",
             "created": "2026-11-04T13:42:25Z", "type": "task", "priority": "2", **fields}
    lines = ["---", *(f"{k}: {v}" for k, v in front.items() if v is not None), "---",
             f"# {title}", "", body]
    if notes:
        lines += ["## Notes", "", notes]
    return "\n".join(lines) + "\n"


def ticket(id_, status="open", *, parent=None, deps=(), priority=2, created=0, type="task"):
    """A Ticket made directly; `created` is in hours after T0, or None."""
    return Ticket(id=id_, title=f"Title {id_}", status=status, type=type, priority=priority,
                  tags=(), deps=tuple(deps), parent=parent,
                  created=None if created is None else T0 + timedelta(hours=created),
                  changed_at=T0, body="", notes=())


def by_id(*tickets):
    return {t.id: t for t in tickets}


class ParseFrontMatter(unittest.TestCase):
    def setUp(self):
        self.t = parse_ticket(TK_FILE, T0)

    def test_a_tk_file_gives_its_front_matter_fields(self):
        self.assertEqual(
            (self.t.id, self.t.status, self.t.type, self.t.priority, self.t.parent),
            ("ter-69s8", "in_progress", "feature", 1, "ter-89xq"))

    def test_square_bracket_values_are_lists(self):
        self.assertEqual(self.t.deps, ("ter-9nrc", "ter-aaaa"))
        self.assertEqual(self.t.tags, ("rendering", "performance", "lights"))

    def test_an_empty_list_is_an_empty_tuple(self):
        self.assertEqual(parse_ticket(text(deps="[]", tags="[ ]"), T0).deps, ())
        self.assertEqual(parse_ticket(text(tags="[ ]"), T0).tags, ())

    def test_a_list_value_without_brackets_is_one_item(self):
        self.assertEqual(parse_ticket(text(deps="t-2"), T0).deps, ("t-2",))

    def test_a_value_with_only_one_bracket_is_one_item(self):
        self.assertEqual(parse_ticket(text(tags="[a"), T0).tags, ("[a",))
        self.assertEqual(parse_ticket(text(tags="a]"), T0).tags, ("a]",))

    def test_missing_lists_are_empty(self):
        t = parse_ticket(text(deps=None), T0)
        self.assertEqual((t.deps, t.tags), ((), ()))

    def test_created_is_an_aware_utc_time(self):
        self.assertEqual(self.t.created, datetime(2026, 11, 4, 13, 42, 25, tzinfo=timezone.utc))

    def test_a_created_time_with_an_offset_or_without_a_zone_becomes_utc(self):
        created = parse_ticket(text(created="2026-11-04T15:42:25+02:00"), T0).created
        self.assertEqual(created, datetime(2026, 11, 4, 13, 42, 25, tzinfo=timezone.utc))
        self.assertEqual(created.tzinfo, timezone.utc)
        self.assertEqual(parse_ticket(text(created="2026-11-04T13:42:25"), T0).created,
                         datetime(2026, 11, 4, 13, 42, 25, tzinfo=timezone.utc))

    def test_a_missing_or_malformed_created_time_is_none(self):
        self.assertIsNone(parse_ticket(text(created=None), T0).created)
        self.assertIsNone(parse_ticket(text(created="yesterday"), T0).created)

    def test_changed_at_is_the_time_given(self):
        self.assertEqual(self.t.changed_at, T0)

    def test_a_missing_or_non_numeric_or_out_of_range_priority_is_2(self):
        for value in (None, "high", "", "5", "-1"):
            with self.subTest(value=value):
                self.assertEqual(parse_ticket(text(priority=value), T0).priority, 2)

    def test_priorities_0_and_4_are_kept(self):
        self.assertEqual(parse_ticket(text(priority="0"), T0).priority, 0)
        self.assertEqual(parse_ticket(text(priority="4"), T0).priority, 4)

    def test_a_missing_or_empty_parent_is_none(self):
        self.assertIsNone(parse_ticket(text(), T0).parent)
        self.assertIsNone(parse_ticket(text(parent=""), T0).parent)

    def test_a_missing_status_is_open_and_a_missing_type_is_task(self):
        t = parse_ticket(text(status=None, type=None), T0)
        self.assertEqual((t.status, t.type), ("open", "task"))

    def test_a_value_keeps_everything_after_the_first_colon(self):
        self.assertEqual(parse_ticket(text(parent="a: b"), T0).parent, "a: b")

    def test_a_front_matter_line_without_a_colon_is_skipped(self):
        t = parse_ticket("---\nid: t-1\nnonsense\nstatus: closed\n---\n# T\n", T0)
        self.assertEqual((t.id, t.status), ("t-1", "closed"))

    def test_windows_line_ends_are_read_as_line_ends(self):
        t = parse_ticket(text(body="b").replace("\n", "\r\n"), T0)
        self.assertEqual((t.id, t.title, t.body), ("t-1", "A title", "b"))


class ParseRejects(unittest.TestCase):
    def test_a_file_without_front_matter_gives_none(self):
        self.assertIsNone(parse_ticket("# Just a title\n\nid: t-1\n", T0))

    def test_an_empty_file_gives_none(self):
        self.assertIsNone(parse_ticket("", T0))

    def test_a_half_written_file_whose_front_matter_never_closes_gives_none(self):
        self.assertIsNone(parse_ticket("---\nid: t-1\nstatus: op", T0))

    def test_an_empty_front_matter_gives_none_even_with_a_second_block_after_it(self):
        self.assertIsNone(parse_ticket("---\n---\nid: t-1\n---\n# T\n", T0))

    def test_a_front_matter_without_an_id_gives_none(self):
        self.assertIsNone(parse_ticket("---\nstatus: open\n---\n# T\n", T0))
        self.assertIsNone(parse_ticket("---\nid: \n---\n# T\n", T0))


class ParseText(unittest.TestCase):
    def test_the_title_is_the_first_heading_after_the_front_matter(self):
        self.assertEqual(parse_ticket(TK_FILE, T0).title, "Per-tile light budget: a title")

    def test_a_ticket_without_a_title_line_takes_its_id_as_title(self):
        t = parse_ticket("---\nid: t-1\n---\n\nJust text.\n", T0)
        self.assertEqual((t.title, t.body), ("t-1", "Just text."))

    def test_a_ticket_of_only_front_matter_has_its_id_as_title_and_no_body(self):
        for tail in ("", "\n\n  \n"):
            with self.subTest(tail=tail):
                t = parse_ticket("---\nid: t-1\n---" + tail, T0)
                self.assertEqual((t.title, t.body, t.notes), ("t-1", "", ()))

    def test_body_text_right_under_the_title_line_is_kept(self):
        self.assertEqual(parse_ticket("---\nid: t-1\n---\n# T\nfirst\nsecond\n", T0).body,
                         "first\nsecond")

    def test_the_body_is_the_text_between_the_title_and_the_notes(self):
        self.assertEqual(parse_ticket(TK_FILE, T0).body, "Body text.\n\n## Design\n\nMore body.")

    def test_each_timestamp_line_in_the_notes_starts_a_note_oldest_first(self):
        self.assertEqual(parse_ticket(TK_FILE, T0).notes, (
            Note(datetime(2026, 11, 4, 14, 17, 19, tzinfo=timezone.utc), "First note."),
            Note(datetime(2026, 11, 5, 9, tzinfo=timezone.utc), "Second note,\ntwo lines.")))

    def test_a_ticket_without_notes_has_none(self):
        self.assertEqual(parse_ticket(text(body="b"), T0).notes, ())

    def test_a_bold_line_that_is_not_a_timestamp_stays_in_the_note(self):
        t = parse_ticket(text(notes="**2026-11-12T10:00:00Z**\n\na\n\n**2026-11-12, the build**\n"
                                    "\nb\n\n**2026-11-09**\n"), T0)
        self.assertEqual(t.notes, (Note(datetime(2026, 11, 12, 10, tzinfo=timezone.utc),
                                        "a\n\n**2026-11-12, the build**\n\nb\n\n**2026-11-09**"),))

    def test_a_timestamp_that_is_not_a_valid_time_stays_in_the_note(self):
        t = parse_ticket(text(notes="**2026-11-12T10:00:00Z**\n\na\n\n**2026-13-45T99:00Z**\n"), T0)
        self.assertEqual(t.notes[0].text, "a\n\n**2026-13-45T99:00Z**")

    def test_notes_come_oldest_first_even_when_the_file_has_them_out_of_order(self):
        t = parse_ticket(text(notes="**2026-11-12T10:00:00Z**\n\nlate\n\n"
                                    "**2026-11-11T10:00:00Z**\n\nearly\n"), T0)
        self.assertEqual([n.text for n in t.notes], ["early", "late"])

    def test_a_timestamp_without_a_zone_is_utc(self):
        t = parse_ticket(text(notes="**2026-11-12T10:00:00**\n\na\n"), T0)
        self.assertEqual(t.notes[0].at, datetime(2026, 11, 12, 10, tzinfo=timezone.utc))

    def test_a_notes_heading_inside_a_fenced_block_stays_in_the_body(self):
        body = "Before.\n\n```text\n## Notes\n\n**2026-11-01T00:00:00Z**\n\nold\n```\n\nAfter."
        t = parse_ticket(text(body=body, notes="**2026-11-12T10:00:00Z**\n\nreal\n"), T0)
        self.assertEqual(t.body, body)
        self.assertEqual(t.notes, (Note(datetime(2026, 11, 12, 10, tzinfo=timezone.utc),
                                        "real"),))

    def test_notes_written_without_blank_lines_keep_every_line(self):
        t = parse_ticket(text(body="b") + "## Notes\n**2026-11-12T10:00:00Z**\nn1\n"
                         "**2026-11-12T11:00:00Z**\nn2\n", T0)
        self.assertEqual([n.text for n in t.notes], ["n1", "n2"])
        self.assertEqual(t.body, "b")

    def test_a_fence_that_never_closes_keeps_a_later_notes_heading_in_the_body(self):
        body = "```\n## Notes\n**2026-11-12T10:00:00Z**\nx"
        t = parse_ticket(text(body=body), T0)
        self.assertEqual((t.body, t.notes), (body, ()))

    def test_a_notes_heading_right_under_a_closing_fence_starts_the_notes(self):
        t = parse_ticket(text(body="```\nx\n```") + "## Notes\n**2026-11-12T10:00:00Z**\nn\n",
                         T0)
        self.assertEqual((t.body, [n.text for n in t.notes]), ("```\nx\n```", ["n"]))

    def test_a_timestamp_line_inside_a_fenced_block_in_a_note_stays_in_the_note(self):
        note = "a\n\n```\n**2026-11-13T00:00:00Z**\n```"
        t = parse_ticket(text(notes=f"**2026-11-12T10:00:00Z**\n\n{note}\n"), T0)
        self.assertEqual([n.text for n in t.notes], [note])

    def test_text_in_the_notes_before_the_first_timestamp_stays_in_the_body(self):
        t = parse_ticket(text(body="b", notes="loose\n\n**2026-11-12T10:00:00Z**\n\nn\n"), T0)
        self.assertEqual(t.body, "b\n\n## Notes\n\nloose")
        self.assertEqual([n.text for n in t.notes], ["n"])

    def test_a_notes_section_without_any_timestamp_stays_in_the_body(self):
        t = parse_ticket(text(body="b", notes="**2026-11-12, the build**\n\nx\n"), T0)
        self.assertEqual(t.body, "b\n\n## Notes\n\n**2026-11-12, the build**\n\nx")
        self.assertEqual(t.notes, ())

    def test_an_empty_notes_heading_is_dropped(self):
        t = parse_ticket(text(body="b") + "## Notes\n\n", T0)
        self.assertEqual((t.body, t.notes), ("b", ()))

    def test_only_the_first_notes_heading_starts_the_notes(self):
        t = parse_ticket(text(notes="**2026-11-12T10:00:00Z**\n\na\n\n## Notes\n\nb\n"), T0)
        self.assertEqual([n.text for n in t.notes], ["a\n\n## Notes\n\nb"])


def write(folder, name, content, mtime):
    """Writes a file and sets its modification time to T0 + `mtime` seconds."""
    path = pathlib.Path(folder) / name
    path.write_text(content)
    stamp = int((T0.timestamp() + mtime) * 1e9)
    os.utime(path, ns=(stamp, stamp))
    return path


def set_mtime(path, seconds):
    stamp = int((T0.timestamp() + seconds) * 1e9)
    os.utime(path, ns=(stamp, stamp))


class StoreTest(unittest.TestCase):
    """A project root folder and three other ticket folders in a temporary directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = pathlib.Path(tmp.name)
        self.root, self.a, self.b, self.c = (self.base / n / ".tickets"
                                             for n in ("root", "a", "b", "c"))
        for folder in (self.root, self.a, self.b, self.c):
            folder.mkdir(parents=True)
        self.listed = [self.root, self.a, self.b, self.c]

    def store(self, per_poll=2):
        store = TicketStore(lambda: list(self.listed), per_poll=per_poll)
        self.addCleanup(store.check_invariants)
        return store

    def change_in_place(self, folder, name, content, mtime):
        """Rewrites a file in place, as `tk add-note` does: the folder's time stays."""
        before = (folder.stat().st_atime_ns, folder.stat().st_mtime_ns)
        write(folder, name, content, mtime)
        os.utime(folder, ns=before)

    def statuses(self, store):
        return {k: t.status for k, t in store.tickets().items()}


class StoreReading(StoreTest):
    def test_the_first_poll_reads_every_ticket_of_every_folder(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        write(self.c, "t-2.md", text("t-2"), 1)
        store = self.store()
        self.assertTrue(store.poll())
        self.assertEqual(set(store.tickets()), {"t-1", "t-2"})

    def test_a_store_before_its_first_poll_has_no_tickets(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        self.assertEqual(self.store().tickets(), {})

    def test_a_poll_without_any_change_reports_no_change(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        write(self.root, "t-2.md", text("t-2"), 1)
        store = self.store()
        store.poll()
        self.assertFalse(store.poll())
        self.assertEqual(set(store.tickets()), {"t-1", "t-2"})

    def test_a_poll_of_empty_folders_reports_no_change(self):
        self.assertFalse(self.store().poll())

    def test_changed_at_keeps_the_microseconds_of_the_files_time(self):
        path = write(self.root, "t-1.md", text("t-1"), 0)
        stamp = int(T0.timestamp()) * 1_000_000_000 + 1_500_000_999
        os.utime(path, ns=(stamp, stamp))
        store = self.store()
        store.poll()
        self.assertEqual(store.tickets()["t-1"].changed_at,
                         T0 + timedelta(seconds=1, microseconds=500_000))

    def test_changed_at_is_the_files_modification_time_in_utc(self):
        write(self.root, "t-1.md", text("t-1"), 90)
        store = self.store()
        store.poll()
        self.assertEqual(store.tickets()["t-1"].changed_at, T0 + timedelta(seconds=90))

    def test_a_file_that_is_not_markdown_is_ignored(self):
        write(self.root, "t-1.md.tmp", text("t-1"), 1)
        write(self.root, "README", text("t-2"), 1)
        (self.root / "t-3.md").mkdir()
        write(self.root, "t-4.md", text("t-4"), 1)
        store = self.store()
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-4"})

    def test_a_half_written_file_is_skipped_until_it_is_complete(self):
        write(self.root, "t-1.md", "---\nid: t-1\nstat", 1)
        store = self.store()
        self.assertFalse(store.poll())
        self.assertEqual(store.tickets(), {})
        write(self.root, "t-1.md", text("t-1"), 2)
        self.assertTrue(store.poll())
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_a_link_to_a_file_that_is_gone_is_left_out(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        (self.root / "t-0.md").symlink_to(self.base / "gone.md")
        store = self.store()
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_a_file_that_is_not_text_is_read_without_failing(self):
        path = self.root / "t-1.md"
        path.write_bytes(text("t-1").encode() + b"\xff\xfe")
        store = self.store()
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_the_tickets_given_out_are_a_copy(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        store = self.store()
        store.poll()
        store.tickets().clear()
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_a_negative_per_poll_is_refused(self):
        with self.assertRaises(ValueError):
            TicketStore(lambda: [], per_poll=-1)

    def test_a_folder_listed_twice_counts_once(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        self.listed = [self.root, self.root, self.a]
        store = self.store()
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_no_folders_give_no_tickets(self):
        self.listed = []
        store = self.store()
        self.assertFalse(store.poll())
        self.assertEqual(store.tickets(), {})


class StoreRescans(StoreTest):
    def test_a_new_file_in_a_folder_appears_on_the_next_poll(self):
        store = self.store(per_poll=0)
        store.poll()
        write(self.c, "t-1.md", text("t-1"), 1)
        set_mtime(self.c, 5)
        self.assertTrue(store.poll())
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_a_file_changed_in_place_in_the_project_root_shows_on_the_next_poll(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        store = self.store(per_poll=0)
        store.poll()
        self.change_in_place(self.root, "t-1.md", text("t-1", status="closed"), 2)
        self.assertTrue(store.poll())
        self.assertEqual(self.statuses(store), {"t-1": "closed"})

    def test_a_file_changed_in_place_in_another_folder_waits_for_its_turn(self):
        write(self.c, "t-1.md", text("t-1"), 1)
        store = self.store(per_poll=1)
        store.poll()
        self.change_in_place(self.c, "t-1.md", text("t-1", status="closed"), 2)
        seen = []
        for _ in range(3):
            store.poll()
            seen.append(self.statuses(store)["t-1"])
        self.assertEqual(seen[0], "open")
        self.assertEqual(seen[2], "closed")

    def test_without_turns_a_change_in_place_in_another_folder_stays_unseen(self):
        write(self.a, "t-1.md", text("t-1"), 1)
        store = self.store(per_poll=0)
        store.poll()
        self.change_in_place(self.a, "t-1.md", text("t-1", status="closed"), 2)
        for _ in range(4):
            store.poll()
        self.assertEqual(self.statuses(store), {"t-1": "open"})

    def test_when_per_poll_covers_every_other_folder_each_poll_sees_every_change(self):
        for folder, id_ in ((self.a, "t-a"), (self.b, "t-b"), (self.c, "t-c")):
            write(folder, f"{id_}.md", text(id_), 1)
        store = self.store(per_poll=3)
        store.poll()
        for folder, id_ in ((self.a, "t-a"), (self.b, "t-b"), (self.c, "t-c")):
            self.change_in_place(folder, f"{id_}.md", text(id_, status="closed"), 2)
        self.assertTrue(store.poll())
        self.assertEqual(set(self.statuses(store).values()), {"closed"})

    def test_by_default_turns_go_round_two_other_folders_at_a_time_in_list_order(self):
        for folder, id_ in ((self.a, "t-a"), (self.b, "t-b"), (self.c, "t-c")):
            write(folder, f"{id_}.md", text(id_), 1)
        store = TicketStore(lambda: list(self.listed))
        store.poll()  # Reads every folder; its turn was a and b.
        for folder, id_ in ((self.a, "t-a"), (self.b, "t-b"), (self.c, "t-c")):
            self.change_in_place(folder, f"{id_}.md", text(id_, status="closed"), 2)
        store.poll()
        first = {k for k, s in self.statuses(store).items() if s == "closed"}
        store.poll()
        second = {k for k, s in self.statuses(store).items() if s == "closed"}
        self.assertEqual(first, {"t-c", "t-a"})
        self.assertEqual(second, {"t-a", "t-b", "t-c"})

    def test_a_turn_of_more_than_the_other_folders_reads_each_once(self):
        self.listed = [self.root, self.a]
        write(self.a, "t-a.md", text("t-a"), 1)
        store = self.store(per_poll=5)
        store.poll()
        self.change_in_place(self.a, "t-a.md", text("t-a", status="closed"), 2)
        self.assertTrue(store.poll())
        self.assertEqual(self.statuses(store), {"t-a": "closed"})

    def test_a_rescan_does_not_reread_a_file_whose_time_and_size_stayed(self):
        write(self.root, "t-1.md", text("t-1", title="Old"), 1)
        store = self.store()
        store.poll()
        self.change_in_place(self.root, "t-1.md", text("t-1", title="New"), 1)
        self.assertFalse(store.poll())
        self.assertEqual(store.tickets()["t-1"].title, "Old")

    def test_a_rewritten_file_with_the_same_ticket_reports_a_change_of_changed_at(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        store = self.store()
        store.poll()
        self.change_in_place(self.root, "t-1.md", text("t-1"), 9)
        self.assertTrue(store.poll())
        self.assertEqual(store.tickets()["t-1"].changed_at, T0 + timedelta(seconds=9))


class StoreMerging(StoreTest):
    def test_the_newest_file_wins_for_an_id(self):
        write(self.root, "t-1.md", text("t-1", status="open"), 1)
        write(self.b, "t-1.md", text("t-1", status="closed"), 2)
        store = self.store()
        store.poll()
        self.assertEqual(self.statuses(store), {"t-1": "closed"})

    def test_an_older_file_in_a_later_folder_loses(self):
        write(self.root, "t-1.md", text("t-1", status="open"), 2)
        write(self.b, "t-1.md", text("t-1", status="closed"), 1)
        store = self.store()
        store.poll()
        self.assertEqual(self.statuses(store), {"t-1": "open"})

    def test_files_with_the_same_text_keep_their_own_times(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        write(self.a, "t-1.md", text("t-1"), 5)
        store = self.store(per_poll=0)
        store.poll()
        self.assertEqual(store.tickets()["t-1"].changed_at, T0 + timedelta(seconds=5))
        (self.a / "t-1.md").unlink()
        set_mtime(self.a, 9)
        store.poll()
        self.assertEqual(store.tickets()["t-1"].changed_at, T0 + timedelta(seconds=1))

    def test_a_tie_goes_to_the_earlier_folder(self):
        write(self.root, "t-1.md", text("t-1", status="open"), 1)
        write(self.a, "t-1.md", text("t-1", status="closed"), 1)
        store = self.store()
        store.poll()
        self.assertEqual(self.statuses(store), {"t-1": "open"})

    def test_a_tie_inside_one_folder_goes_to_the_first_file_name(self):
        write(self.root, "b.md", text("t-1", status="closed"), 1)
        write(self.root, "a.md", text("t-1", status="open"), 1)
        store = self.store()
        store.poll()
        self.assertEqual(self.statuses(store), {"t-1": "open"})

    def test_a_change_to_a_copy_that_still_loses_reports_no_change(self):
        write(self.root, "t-1.md", text("t-1"), 10)
        write(self.a, "t-1.md", text("t-1", status="closed"), 1)
        store = self.store()
        store.poll()
        self.change_in_place(self.a, "t-1.md", text("t-1", status="in_progress"), 2)
        self.assertFalse(store.poll())
        self.assertEqual(self.statuses(store), {"t-1": "open"})

    def test_when_the_newest_copy_disappears_the_older_copy_wins_again(self):
        write(self.root, "t-1.md", text("t-1", status="open"), 1)
        write(self.a, "t-1.md", text("t-1", status="closed"), 2)
        store = self.store()
        store.poll()
        (self.a / "t-1.md").unlink()
        set_mtime(self.a, 7)
        self.assertTrue(store.poll())
        self.assertEqual(self.statuses(store), {"t-1": "open"})

    def test_a_ticket_whose_last_file_disappears_drops_out(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        store = self.store()
        store.poll()
        (self.root / "t-1.md").unlink()
        self.assertTrue(store.poll())
        self.assertEqual(store.tickets(), {})


class StoreFolders(StoreTest):
    def test_a_folder_that_disappears_drops_its_tickets(self):
        write(self.b, "t-1.md", text("t-1"), 1)
        store = self.store(per_poll=0)
        store.poll()
        shutil.rmtree(self.b.parent)
        self.assertTrue(store.poll())
        self.assertEqual(store.tickets(), {})

    def test_a_folder_that_is_no_longer_listed_drops_its_tickets(self):
        write(self.b, "t-1.md", text("t-1"), 1)
        store = self.store()
        store.poll()
        self.listed = [self.root, self.a]
        self.assertTrue(store.poll())
        self.assertEqual(store.tickets(), {})

    def test_a_listed_folder_that_appears_later_is_read_when_it_appears(self):
        late = self.base / "late" / ".tickets"
        self.listed.append(late)
        store = self.store(per_poll=0)
        self.assertFalse(store.poll())
        late.mkdir(parents=True)
        write(late, "t-1.md", text("t-1"), 1)
        self.assertTrue(store.poll())
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_a_missing_project_root_does_not_stop_the_other_folders(self):
        shutil.rmtree(self.root)
        write(self.a, "t-1.md", text("t-1"), 1)
        store = self.store()
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-1"})

    def test_a_listed_path_that_is_a_file_gives_no_tickets(self):
        not_a_folder = write(self.base, "file", text("t-1"), 1)
        self.listed = [self.root, not_a_folder]
        store = self.store()
        self.assertFalse(store.poll())
        self.assertEqual(store.tickets(), {})

    def test_a_folder_that_cannot_be_listed_gives_no_tickets(self):
        write(self.a, "t-1.md", text("t-1"), 1)
        store = self.store()
        store.poll()
        os.chmod(self.a, 0o000)
        self.addCleanup(os.chmod, self.a, 0o755)
        set_mtime(self.a, 9)
        self.assertTrue(store.poll())
        self.assertEqual(store.tickets(), {})

    def test_a_file_that_cannot_be_read_is_left_out(self):
        path = write(self.root, "t-1.md", text("t-1"), 1)
        write(self.root, "t-2.md", text("t-2"), 1)
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        store = self.store()
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-2"})
        os.chmod(path, 0o644)
        store.poll()
        self.assertEqual(set(store.tickets()), {"t-1", "t-2"})


class StoreInvariants(StoreTest):
    """check_invariants finds each kind of broken cache."""

    def polled(self):
        write(self.root, "t-1.md", text("t-1"), 1)
        write(self.a, "t-2.md", text("t-2"), 1)
        store = TicketStore(lambda: list(self.listed))
        store.poll()
        store.check_invariants()
        return store

    def test_a_merged_ticket_that_no_file_holds_is_found(self):
        store = self.polled()
        store._tickets["t-9"] = ticket("t-9")
        with self.assertRaisesRegex(AssertionError, "not the merge"):
            store.check_invariants()

    def test_a_merged_ticket_under_the_wrong_id_is_found(self):
        store = self.polled()
        store._tickets["t-1"] = store._tickets["t-2"]
        with self.assertRaisesRegex(AssertionError, "not the merge"):
            store.check_invariants()

    def test_a_cached_folder_that_is_not_listed_is_found(self):
        store = self.polled()
        self.listed = [self.root]
        store._listed = (self.root,)
        with self.assertRaisesRegex(AssertionError, "folder is not listed"):
            store.check_invariants()

    def test_a_cached_ticket_whose_time_is_not_its_files_time_is_found(self):
        store = self.polled()
        folder = store._folders[self.root]
        key, t = folder.files["t-1.md"]
        folder.files["t-1.md"] = (key, replace(t, changed_at=T0))
        store._tickets["t-1"] = folder.files["t-1.md"][1]
        with self.assertRaisesRegex(AssertionError, "time is not its file's time"):
            store.check_invariants()

    def test_a_turn_outside_the_other_folders_is_found(self):
        store = self.polled()
        store._turn = 3
        with self.assertRaisesRegex(AssertionError, "turn is outside"):
            store.check_invariants()

    def test_a_turn_without_other_folders_must_be_0(self):
        self.listed = [self.root]
        store = self.polled()
        store._turn = 1
        with self.assertRaisesRegex(AssertionError, "turn is outside"):
            store.check_invariants()

    def test_a_cached_file_without_a_ticket_breaks_no_invariant(self):
        write(self.root, "broken.md", "no front matter", 1)
        self.polled().check_invariants()


def epic(id_, status="open", **kw):
    return ticket(id_, status, type="epic", **kw)


class Groups(unittest.TestCase):
    """Each ticket belongs to the group of its nearest ancestor of type epic."""

    def test_a_group_holds_the_open_tickets_below_an_epic_through_tasks(self):
        tickets = by_id(epic("e"), ticket("task", parent="e"),
                        ticket("sub", "in_progress", parent="task"))
        [group] = groups(tickets)
        self.assertEqual(group.root.id, "e")
        self.assertEqual({t.id for t in group.members}, {"task", "sub"})

    def test_an_inner_epic_is_a_member_of_its_parent_and_heads_its_own_group(self):
        tickets = by_id(epic("outer", created=1), epic("inner", "in_progress", parent="outer", created=2),
                        ticket("work", "in_progress", parent="inner"),
                        ticket("near", parent="outer"))
        result = {g.root.id: [t.id for t in g.members] for g in groups(tickets)}
        self.assertEqual(result, {"outer": ["inner", "near"], "inner": ["work"]})

    def test_an_inner_epic_without_work_in_progress_shows_only_in_its_parent(self):
        tickets = by_id(epic("outer"), epic("inner", parent="outer"),
                        ticket("work", parent="inner"), ticket("x", "in_progress", parent="outer"))
        self.assertEqual([g.root.id for g in groups(tickets)], ["outer"])

    def test_the_root_is_not_a_member_of_its_own_group(self):
        [group] = groups(by_id(epic("e", "in_progress"), ticket("child", "in_progress", parent="e")))
        self.assertEqual([t.id for t in group.members], ["child"])

    def test_closed_tickets_of_the_group_are_counted_not_listed(self):
        [group] = groups(by_id(epic("e"), ticket("a", "in_progress", parent="e"),
                               ticket("b", "closed", parent="e"),
                               ticket("c", "closed", parent="b")))
        self.assertEqual(([t.id for t in group.members], group.closed), (["a"], 2))

    def test_a_closed_root_does_not_count_as_closed_in_its_group(self):
        [group] = groups(by_id(epic("e", "closed"), ticket("a", "in_progress", parent="e")))
        self.assertEqual((group.root.id, group.closed), ("e", 0))

    def test_only_groups_with_a_member_in_progress_are_given(self):
        tickets = by_id(epic("e1"), ticket("a", parent="e1"),
                        epic("e2"), ticket("b", "in_progress", parent="e2"),
                        epic("e3", "in_progress"), ticket("c", "closed", parent="e3"))
        self.assertEqual([g.root.id for g in groups(tickets)], ["e2"])

    def test_tickets_without_an_epic_above_them_form_the_no_epic_group_last(self):
        tickets = by_id(ticket("loose", "in_progress", created=-10),
                        ticket("done", "closed"),
                        ticket("task", created=-5), ticket("sub", parent="task"),
                        epic("e", created=5), ticket("a", "in_progress", parent="e"))
        result = groups(tickets)
        self.assertEqual([g.root.id if g.root else None for g in result], ["e", None])
        self.assertEqual(result[1], Group(None, (tickets["loose"], tickets["task"], tickets["sub"]), 1))

    def test_an_epic_without_an_epic_above_it_is_in_no_group(self):
        result = groups(by_id(epic("e"), ticket("loose", "in_progress")))
        self.assertEqual([[t.id for t in g.members] for g in result], [["loose"]])

    def test_a_no_epic_group_without_work_in_progress_is_not_given(self):
        self.assertEqual(groups(by_id(ticket("loose"))), [])

    def test_a_ticket_whose_parent_is_missing_has_no_epic_above_it(self):
        [group] = groups(by_id(ticket("orphan", "in_progress", parent="gone")))
        self.assertIsNone(group.root)
        self.assertEqual([t.id for t in group.members], ["orphan"])

    def test_a_parent_cycle_stops_the_search_for_an_epic(self):
        tickets = by_id(ticket("x", "in_progress", parent="y"), epic("y", parent="x"),
                        ticket("p", "in_progress", parent="q"), ticket("q", parent="p"))
        result = {g.root.id if g.root else None: [t.id for t in g.members] for g in groups(tickets)}
        self.assertEqual(result, {"y": ["x"], None: ["p", "q"]})

    def test_groups_come_in_order_of_their_roots_creation_oldest_first(self):
        tickets = by_id(epic("new", created=5), ticket("a", "in_progress", parent="new"),
                        epic("old", created=1), ticket("b", "in_progress", parent="old"),
                        epic("undated", created=None),
                        ticket("c", "in_progress", parent="undated"))
        self.assertEqual([g.root.id for g in groups(tickets)], ["old", "new", "undated"])

    def test_groups_with_roots_of_the_same_time_come_in_order_of_root_id(self):
        tickets = by_id(epic("r2"), ticket("a", "in_progress", parent="r2"),
                        epic("r1"), ticket("b", "in_progress", parent="r1"))
        self.assertEqual([g.root.id for g in groups(tickets)], ["r1", "r2"])


class MemberOrder(unittest.TestCase):
    def order(self, *members):
        tickets = by_id(epic("epic"), *members)
        [group] = groups(tickets)
        return [t.id for t in group.members]

    def test_members_in_progress_come_first_then_ready_then_blocked(self):
        self.assertEqual(self.order(
            ticket("blocked", parent="epic", deps=["work"], priority=0),
            ticket("ready", parent="epic", priority=0),
            ticket("work", "in_progress", parent="epic", priority=4)),
            ["work", "ready", "blocked"])

    def test_a_blocked_ticket_in_progress_still_comes_first(self):
        self.assertEqual(self.order(
            ticket("ready", parent="epic", priority=0),
            ticket("dep", parent="epic", priority=1),
            ticket("work", "in_progress", parent="epic", deps=["dep"], priority=4)),
            ["work", "ready", "dep"])

    def test_within_a_part_members_come_in_order_of_priority_then_creation(self):
        self.assertEqual(self.order(
            ticket("p2-old", "in_progress", parent="epic", priority=2, created=1),
            ticket("p1", "in_progress", parent="epic", priority=1, created=9),
            ticket("p2-new", "in_progress", parent="epic", priority=2, created=5)),
            ["p1", "p2-old", "p2-new"])

    def test_a_member_without_a_creation_time_comes_after_those_with_one(self):
        self.assertEqual(self.order(
            ticket("undated", "in_progress", parent="epic", created=None),
            ticket("dated", "in_progress", parent="epic", created=9)),
            ["dated", "undated"])

    def test_members_alike_in_all_else_come_in_order_of_id(self):
        self.assertEqual(self.order(ticket("b", "in_progress", parent="epic"),
                                    ticket("a", "in_progress", parent="epic")), ["a", "b"])


class BlockedBy(unittest.TestCase):
    def test_a_ticket_is_blocked_by_its_open_and_in_progress_dependencies_in_order(self):
        tickets = by_id(ticket("d1", "in_progress"), ticket("d2", "closed"), ticket("d3"))
        t = ticket("t", deps=["d3", "d2", "d1"])
        self.assertEqual(blocked_by(t, tickets), ("d3", "d1"))

    def test_an_unknown_dependency_does_not_block(self):
        self.assertEqual(blocked_by(ticket("t", deps=["gone"]), {}), ())

    def test_a_dependency_with_another_status_does_not_block(self):
        tickets = by_id(ticket("d", "deferred"))
        self.assertEqual(blocked_by(ticket("t", deps=["d"]), tickets), ())


if __name__ == "__main__":
    unittest.main()
