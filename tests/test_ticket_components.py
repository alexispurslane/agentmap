"""Behavior of the ticket bar: the epic strip, the ticket lists and the ticket text."""

import re
import unittest
from datetime import datetime, timedelta, timezone

from agentmap.components import epic_strip, page, ticket_body, ticket_lists, ticket_peek
from agentmap.components.close.close import close
from agentmap.components.link.link import link
from agentmap.components.view import Forest, element_id, ticket_agents
from agentmap.status import Status
from agentmap.t3 import AgentThread
from agentmap.tickets import Group, Note, Ticket, groups

from tests.test_components import CSS, CSS_CLASSES, check

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


def ago(minutes):
    return NOW - timedelta(minutes=minutes)


def ticket(id, title=None, status="open", *, parent=None, deps=(), priority=2, type="task",
           tags=(), changed=30, created=600, body="", notes=()):
    return Ticket(id=id, title=title or f"Title of {id}", status=status, type=type,
                  priority=priority, tags=tuple(tags), deps=tuple(deps), parent=parent,
                  created=ago(created), changed_at=ago(changed), body=body,
                  notes=tuple(Note(ago(m), text) for m, text in notes))


def thread(id, title, parent=None, created=60):
    return AgentThread(id=id, parent_id=parent, title=title, provider="claude", model=None,
                       state="working", created_at=ago(created), updated_at=NOW, now=None,
                       thought=None, last_said=None, thought_at=None, said_at=None, touched=())


def board(tickets, threads=(), statuses=None):
    """The two ticket fragments for these tickets and threads."""
    by_id = {t.id: t for t in tickets}
    forest = Forest(list(threads))
    links = ticket_agents(forest, statuses or {}, by_id.keys())
    found = groups(by_id)
    return (epic_strip(found, by_id, links, forest, NOW),
            ticket_lists(found, by_id, links, forest, NOW))


EPIC = [
    ticket("ter-89xq", "Light budget", "in_progress", type="epic", priority=1, tags=["lights"],
           created=900, changed=30),
    ticket("ter-69s8", "Per-tile cap", "in_progress", parent="ter-89xq", priority=1,
           deps=["ter-9nrc"], changed=2, body="The **cap** keeps `K` lights.\n\nMore."),
    ticket("ter-9nrc", "Remove sampling", "closed", parent="ter-89xq"),
    ticket("ter-bqun", "View cap", "open", parent="ter-89xq", priority=0, deps=["ter-69s8"]),
    ticket("ter-aaaa", "Ready step", "open", parent="ter-89xq", priority=3,
           notes=[(30, "First."), (5, "Second.")]),
]


# The attributes of a ticket mark that the thread A (hue 75.0) works on: a hover
# focuses A and peeks at the ticket.
PEEK = ('data-on:mouseover__stop="$focus = \' A \'; $hue = \'75.0\'; '
        '$peek == \'{dom}\' || ($peek = \'{dom}\', @get(\'/peek/{url}\'))" '
        'data-on:mouseleave="$focus = \'\'; $peek = \'\'" '
        'data-class:hl="$focus.includes(\' A \')"')
WORKER = [thread("t1", "ter-69s8: build the cap")]


def column(strip, key):
    m = re.search(rf'<button class="epic[^"]*" id="{element_id("g", key)}".*?</button>', strip, re.S)
    assert m, key
    return m.group(0)


def rows(lists, key):
    """(dom id, classes) of each ticket row in the list of group `key`, top to bottom."""
    m = re.search(rf'<section class="mod tickets" id="{element_id("g", key)}".*?</section>', lists, re.S)
    assert m, key
    return re.findall(r'<article class="tk ([^"]*)" id="([^"]+)"', m.group(0))


def row(lists, ticket_id):
    dom = element_id("t", ticket_id)
    m = re.search(rf'<article class="tk [^"]*" id="{dom}".*?</article>', lists, re.S)
    assert m, ticket_id
    return m.group(0)


class EpicStripTest(unittest.TestCase):
    def test_the_strip_is_its_own_fragment_with_one_column_per_active_group(self):
        strip, _ = board(EPIC + [ticket("quiet", type="epic"), ticket("q1", parent="quiet")])
        self.assertTrue(strip.startswith('<section id="epics" class="mod">'))
        self.assertEqual(len(re.findall(r'<button class="epic', strip)), 1)
        self.assertIn("<small>1 epic · 1 in progress</small>", strip)

    def test_a_column_shows_the_epic_id_priority_title_and_counts(self):
        col = column(board(EPIC)[0], "ter-89xq")
        self.assertIn('<span class="ep-id">ter-89xq</span>', col)
        self.assertIn('<span class="prio p1">P1</span>', col)
        self.assertIn('<span class="ep-title">Light budget</span>', col)
        self.assertIn("<b>1</b> in progress · 1 ready · 1 blocked · 1 closed", col)
        self.assertIn('<span class="tags">lights</span>', col)

    def test_a_column_lists_its_tickets_in_progress_with_their_agents(self):
        col = column(board(EPIC, [thread("t1", "ter-69s8: build")])[0], "ter-89xq")
        self.assertRegex(col, r'<span class="ep-now"><span class="now-item" [^>]*><span class="tk-id">ter-69s8</span>'
                              r'<span class="tag" [^>]*>A</span><span class="now-title">Per-tile cap</span></span></span>')

    def test_a_column_lists_at_most_five_tickets_in_progress_then_a_count(self):
        many = [ticket(f"t{i}", status="in_progress", parent="ter-89xq") for i in range(8)]
        col = column(board(EPIC[:1] + many)[0], "ter-89xq")
        self.assertEqual(col.count('class="now-item"'), 5)
        self.assertIn('<span class="now-more">and 3 more</span>', col)

    def test_hovering_a_ticket_in_progress_peeks_at_it(self):
        col = column(board(EPIC, WORKER)[0], "ter-89xq")
        dom = element_id("t", "ter-69s8")
        self.assertIn(f'<span class="now-item" {PEEK.format(dom=dom, url="ter-69s8")}>', col)

    def test_the_bar_shows_the_closed_and_in_progress_shares(self):
        col = column(board(EPIC)[0], "ter-89xq")
        self.assertIn('<span class="done" style="width:25.0%"></span>'
                      '<span class="active" style="width:25.0%"></span>', col)

    def test_a_column_is_inked_by_its_newest_change(self):
        self.assertRegex(column(board(EPIC)[0], "ter-89xq"), r'^<button class="epic a1"')
        self.assertIn('<span class="changed">changed 2m ago</span>', column(board(EPIC)[0], "ter-89xq"))

    def test_a_change_under_a_minute_old_reads_changed_now(self):
        strip, _ = board([ticket("a", status="in_progress", changed=0)])
        self.assertIn('<span class="changed">changed now</span>', strip)

    def test_a_click_on_a_column_opens_its_list_and_closes_any_open_ticket(self):
        key = element_id("g", "ter-89xq")
        col = column(board(EPIC)[0], "ter-89xq")
        self.assertIn(f"data-on:click=\"$epic = $epic == '{key}' ? '' : '{key}'; $ticket = ''\"", col)
        self.assertIn(f"data-class:sel=\"$epic == '{key}'\"", col)

    def test_tickets_without_parent_or_children_form_the_no_epic_column(self):
        strip, _ = board([ticket("lone", status="in_progress")])
        col = column(strip, "")
        self.assertIn('<span class="ep-title">No epic</span>', col)

    def test_an_empty_strip_says_that_no_ticket_is_in_progress(self):
        strip, _ = board([ticket("a")])
        self.assertIn('<p class="empty">No ticket is in progress.</p>', strip)


class AgentLinkTest(unittest.TestCase):
    def test_a_thread_works_on_the_tickets_that_its_title_or_status_names(self):
        forest = Forest([thread("t1", "ter-69s8: build the cap"), thread("t2", "Other work", created=50)])
        links = ticket_agents(forest, {"t2": Status("ter-bqun: waiting", NOW)},
                              {"ter-69s8", "ter-bqun", "ter-aaaa"})
        self.assertEqual(links, {"ter-69s8": ["t1"], "ter-bqun": ["t2"]})

    def test_a_word_that_only_looks_like_an_id_links_nothing(self):
        forest = Forest([thread("t1", "Firefly-robust rect-lamps (ter-69s8)")])
        self.assertEqual(ticket_agents(forest, {}, {"ter-69s8"}), {"ter-69s8": ["t1"]})

    def test_the_column_and_the_row_show_the_linked_agents_tags(self):
        strip, lists = board(EPIC, [thread("t1", "ter-69s8: build the cap")])
        for html in (column(strip, "ter-89xq"), row(lists, "ter-69s8")):
            self.assertRegex(html, r'<span class="tag" style="--hue:75.0" data-on:mouseover__stop="\$focus = \' A \'')
        self.assertNotIn('class="tag"', row(lists, "ter-bqun"))

    def test_a_thread_that_names_the_epic_counts_for_its_column(self):
        strip, _ = board(EPIC, [thread("t1", "ter-89xq: orchestrate")])
        self.assertIn("$focus = ' A '", column(strip, "ter-89xq"))

    def test_hovering_an_epic_column_focuses_the_agents_that_work_on_it(self):
        strip, _ = board(EPIC, [thread("t1", "ter-69s8: build"), thread("t2", "ter-bqun: wait", created=50)])
        self.assertIn('id="' + element_id("g", "ter-89xq") + '" data-on:mouseover__stop='
                      '"$focus = \' A B \'; $hue = \'75.0\'" data-on:mouseleave="$focus = \'\'" '
                      'data-class:hl="$focus.includes(\' A \') || $focus.includes(\' B \')"', column(strip, "ter-89xq"))

    def test_a_ticket_mark_without_agents_focuses_nothing_and_never_lights(self):
        r = row(board(EPIC)[1], "ter-bqun")
        self.assertIn('data-on:mouseover__stop="$focus = \'\'; $hue = \'\';', r)
        self.assertIn('data-class:hl="false"', r)

    def test_ticket_marks_light_in_the_hue_of_the_focused_agent(self):
        for mark in (".epic.hl", ".now-item.hl", ".tk-head.hl"):
            rule = re.search(re.escape(mark) + r"[^{]*\{([^}]*)\}", CSS)
            self.assertIsNotNone(rule, mark)
            self.assertIn("var(--focus-hue)", rule.group(1))


class TicketListTest(unittest.TestCase):
    def test_the_lists_are_their_own_fragment_one_hidden_list_per_group(self):
        _, lists = board(EPIC)
        key = element_id("g", "ter-89xq")
        self.assertTrue(lists.startswith('<div id="tickets">'))
        self.assertIn(f'<section class="mod tickets" id="{key}" data-show="$epic == \'{key}\'" '
                      'style="display:none">', lists)

    def test_rows_are_in_progress_then_ready_then_blocked(self):
        _, lists = board(EPIC)
        self.assertEqual([(cls.split()[0], dom) for cls, dom in rows(lists, "ter-89xq")],
                         [("in_progress", element_id("t", "ter-69s8")),
                          ("ready", element_id("t", "ter-aaaa")),
                          ("blocked", element_id("t", "ter-bqun"))])

    def test_a_row_shows_its_metadata_line_then_its_title_on_a_line_of_its_own(self):
        r = row(board(EPIC)[1], "ter-bqun")
        self.assertIn('<span class="tk-meta"><span class="mark"></span><span class="tk-id">ter-bqun</span>'
                      '<span class="prio p0">P0</span><span class="type">task</span>'
                      '<span class="blockers">blocked by ter-69s8</span></span>'
                      '<span class="tk-title">View cap</span></button>', r)

    def test_hovering_a_row_peeks_at_its_ticket(self):
        dom = element_id("t", "ter-69s8")
        self.assertIn(PEEK.format(dom=dom, url="ter-69s8"), row(board(EPIC, WORKER)[1], "ter-69s8"))

    def test_a_row_shows_its_note_count_and_newest_note_age(self):
        self.assertIn('<span class="notes a2">2 notes · 5m ago</span>', row(board(EPIC)[1], "ter-aaaa"))
        self.assertNotIn('class="notes', row(board(EPIC)[1], "ter-bqun"))

    def test_a_row_shows_no_preview_of_its_text(self):
        r = row(board(EPIC)[1], "ter-69s8")
        self.assertNotIn("excerpt", r)
        self.assertNotIn("keeps", r)

    def test_a_click_on_a_row_toggles_it_and_loads_its_text(self):
        dom = element_id("t", "ter-69s8")
        r = row(board(EPIC)[1], "ter-69s8")
        self.assertIn(f"data-on:click=\"$ticket = $ticket == '{dom}' ? '' : '{dom}'; "
                      f"$ticket == '{dom}' &amp;&amp; @get('/ticket/ter-69s8')\"", r)
        self.assertIn(f'<div class="tk-body" id="tb-{dom}" data-ignore-morph '
                      f'data-show="$ticket == \'{dom}\'" style="display:none"></div>', r)

    def test_an_id_with_unsafe_characters_is_quoted_in_the_url(self):
        r = row(board([ticket("odd'id", status="in_progress")])[1], "odd'id")
        self.assertIn("@get('/ticket/odd%27id')", r)

    def test_a_list_has_a_close_button_and_a_note_for_a_group_that_is_gone(self):
        _, lists = board(EPIC)
        self.assertIn('<button class="close" data-on:click="$epic = \'\'; $ticket = \'\'" '
                      'aria-label="Close">✕</button>', lists)
        key = element_id("g", "ter-89xq")
        self.assertIn(f"data-show=\"$epic != '' &amp;&amp; !['{key}'].includes($epic)\"", lists)

    def test_the_close_button_clears_the_open_epic_and_ticket(self):
        self.assertEqual(close(), '<button class="close" data-on:click="$epic = \'\'; $ticket = \'\'" '
                                  'aria-label="Close">✕</button>')

    def test_the_event_log_hides_while_an_epic_is_open(self):
        page_html = page('<main id="app"><section id="log" class="mod" data-show="$epic == \'\'"></section></main>', "", "")
        self.assertIn("data-show=\"$epic == ''\"", page_html)


class TicketBodyTest(unittest.TestCase):
    def test_the_text_is_rendered_markdown_then_the_notes_newest_first(self):
        html = ticket_body(ticket("x", body="Some **bold**.", notes=[(30, "First."), (5, "Second.")]))
        self.assertIn("<p>Some <strong>bold</strong>.</p>", html)
        self.assertLess(html.index("Second."), html.index("First."))
        self.assertIn(f"<time>{ago(5).astimezone().strftime('%Y-%m-%d %H:%M')}</time>", html)

    def test_a_ticket_without_text_says_so(self):
        self.assertIn('<p class="empty">No text.</p>', ticket_body(ticket("x")))


class TicketPeekTest(unittest.TestCase):
    def test_the_peek_shows_the_title_and_the_rendered_description_but_no_notes(self):
        html = ticket_peek(ticket("x", "Per-tile cap", body="The **cap**.", notes=[(5, "A note.")]))
        self.assertIn('<h3 class="peek-title">Per-tile cap</h3><div class="md"><p>The <strong>cap</strong>.</p>', html)
        self.assertNotIn("A note.", html)

    def test_the_peek_shows_only_while_its_ticket_is_hovered(self):
        dom = element_id("t", "x")
        self.assertTrue(ticket_peek(ticket("x")).startswith(f'<div data-show="$peek == \'{dom}\'">'))

    def test_a_ticket_without_a_description_peeks_with_its_title_and_says_so(self):
        self.assertIn('<p class="empty">No description.</p>', ticket_peek(ticket("x")))

    def test_the_link_attributes_quote_the_id_in_the_url(self):
        self.assertEqual(link(["A"], 75.0, "odd'id"), PEEK.format(dom=element_id("t", "odd'id"), url="odd%27id"))

    def test_the_peek_box_is_placed_next_to_the_hovered_ticket_by_css_anchors(self):
        # The element under the pointer is the anchor. A ticket can show in its
        # epic column and in the open list at once, so a class per ticket would
        # make two anchors.
        self.assertIn(".now-item:hover, .tk-head:hover { anchor-name: --peek; }", CSS)
        self.assertIn("position-anchor: --peek;", CSS)
        self.assertIn("position-try-fallbacks: flip-block, flip-inline;", CSS)


class PageTest(unittest.TestCase):
    def test_the_page_has_one_peek_box_outside_the_fragments(self):
        html = page('<main id="app"></main>', '<section id="epics"></section>', '<div id="tickets"></div>')
        self.assertIn('<aside id="peek" class="peek" '
                      'data-class:open="$peek != \'\' &amp;&amp; $peek != $ticket" aria-hidden="true"></aside>', html)
        self.assertLess(html.index('id="epics"'), html.index('id="peek"'))

    def test_the_body_declares_three_signals_and_holds_three_fragments(self):
        html = page('<main id="app"></main>', '<section id="epics"></section>', '<div id="tickets"></div>')
        body = re.search(r"<body[^>]*>", html).group(0)
        self.assertIn("data-signals=\"{focus: '', hue: '', epic: '', ticket: '', peek: '', now: Date.now() / 1000}\"", body)
        self.assertLess(html.index('id="app"'), html.index('id="tickets"'))
        self.assertLess(html.index('id="tickets"'), html.index('id="epics"'))


class MarkupTest(unittest.TestCase):
    def test_the_fragments_are_well_formed_and_every_class_has_a_style(self):
        strip, lists = board(EPIC + [ticket("lone", status="in_progress")],
                             [thread("t1", "ter-69s8: build")])
        classes = set()
        for html in (strip, lists, ticket_body(EPIC[1]), ticket_peek(EPIC[1]), ticket_peek(EPIC[0]), board([])[0]):
            c = check(html)
            self.assertEqual(c.errors, [])
            classes |= c.classes
        # Row states that keep the default look on purpose.
        self.assertEqual(classes - CSS_CLASSES - {"in_progress", "ready"}, set())

    def test_ticket_text_is_escaped(self):
        strip, lists = board([ticket("x", "<b>t</b>", "in_progress", body="<script>", tags=["<i>"])])
        for html in (strip, lists, ticket_body(ticket("x", body="<script>")),
                     ticket_peek(ticket("x", "<b>t</b>", body="<script>"))):
            self.assertNotIn("<script>", html)
            self.assertNotIn("<b>t", html)


if __name__ == "__main__":
    unittest.main()
