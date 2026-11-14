"""Behavior of the HTML that the dashboard sends to the page."""

import pathlib
import re
import unittest
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone

from agentmap.plan import Item, Plan
from agentmap.render import (
    MAP_H,
    MAP_W,
    format_age,
    render_app,
    render_page,
)
from agentmap.t3 import AgentThread
from agentmap.treemap import Cell

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


def thread(id, parent=None, *, title=None, created=60, updated=0, state="working",
           now=None, thought=None, last_said=None, touched=(), provider="claude",
           model="claude-opus-5-5"):
    """A thread created `created` minutes and updated `updated` minutes before NOW."""
    return AgentThread(
        id=id, parent_id=parent, title=title or f"Thread {id}", provider=provider,
        model=model, state=state, created_at=NOW - timedelta(minutes=created),
        updated_at=NOW - timedelta(minutes=updated), now=now, thought=thought,
        last_said=last_said, touched=tuple(touched))


CELLS = [
    Cell("src", "src", 1, 0, 0, 600, 600, 100),
    Cell("src/a", "a", 2, 0, 16, 300, 584, 50),
    Cell("src/b", "b", 2, 300, 16, 300, 584, 50),
    Cell("docs", "docs", 1, 600, 0, 480, 600, 40),
]


def app(threads, plans=None, cells=CELLS):
    return render_app(threads, plans or {}, cells, NOW, "demo")


def card(html, tag):
    """The HTML of the card with the given tag, up to the next card."""
    m = re.search(rf'<article[^>]*data-tag="{re.escape(tag)}".*?</article>', html, re.S)
    assert m, f"no card with tag {tag}"
    return m.group(0)


def dots_in_cell(html, key):
    m = re.search(rf'<div[^>]*data-cell="{re.escape(key)}".*?</div><!--/cell-->', html, re.S)
    assert m, f"no cell {key}"
    return re.findall(r'class="dot[^"]*"[^>]*>([^<]+)<', m.group(0))


class PageTest(unittest.TestCase):
    def test_page_opens_the_event_stream_when_it_loads(self):
        page = render_page("<main id=\"app\"></main>")
        self.assertIn("data-init=\"@get('/stream', {openWhenHidden: true})\"", page)
        self.assertIn('src="/static/datastar.js"', page)
        self.assertIn('<main id="app"></main>', page)

    def test_app_is_one_main_element_with_a_map_and_a_tree(self):
        html = app([thread("t1")])
        self.assertTrue(html.startswith('<main id="app"'))
        self.assertTrue(html.rstrip().endswith("</main>"))
        self.assertIn('<section id="map"', html)
        self.assertIn('<section id="tree"', html)


class TagTest(unittest.TestCase):
    def test_root_threads_get_letters_in_order_of_creation(self):
        html = app([thread("late", created=10), thread("early", created=90)])
        self.assertIn("Thread early", card(html, "A"))
        self.assertIn("Thread late", card(html, "B"))

    def test_children_get_the_parent_tag_and_their_position(self):
        html = app([
            thread("root", created=90),
            thread("c2", "root", created=20),
            thread("c1", "root", created=40),
            thread("g1", "c2", created=10),
        ])
        self.assertIn("Thread c1", card(html, "A1"))
        self.assertIn("Thread c2", card(html, "A2"))
        self.assertIn("Thread g1", card(html, "A2.1"))

    def test_a_child_card_sits_inside_its_parent_card(self):
        html = app([thread("root"), thread("kid", "root")])
        self.assertIn('data-tag="A1"', card(html, "A"))

    def test_descendants_share_the_hue_of_their_root(self):
        html = app([thread("r1", created=90), thread("r2", created=80),
                    thread("k", "r2"), thread("g", "k")])
        hue = lambda tag: re.search(r'--hue:\s*([^;"]+)', card(html, tag)).group(1)
        self.assertNotEqual(hue("A"), hue("B"))
        self.assertEqual(hue("B"), hue("B1"))
        self.assertEqual(hue("B"), hue("B1.1"))


class MapTest(unittest.TestCase):
    def test_cells_are_placed_in_percent_of_the_map(self):
        html = app([thread("t")], cells=[Cell("src", "src", 1, MAP_W / 4, 0, MAP_W / 2, MAP_H, 1)])
        self.assertRegex(html, r'data-cell="src"[^>]*style="left:25(\.0+)?%;top:0(\.0+)?%;'
                               r'width:50(\.0+)?%;height:100(\.0+)?%')

    def test_a_cell_of_files_directly_in_a_directory_is_labeled_files(self):
        html = app([], cells=[Cell(".", ".", 1, 0, 0, 100, 100, 1),
                              Cell("src/.", ".", 2, 100, 0, 100, 100, 1)])
        self.assertEqual(re.findall(r'<span class="label">([^<]*)</span>', html), ["files", "files"])

    def test_a_thread_has_a_dot_in_the_cell_of_its_newest_touched_path(self):
        html = app([thread("t", touched=["src/b/x.py", "docs/y.md"])])
        self.assertEqual(dots_in_cell(html, "src/b"), ["A"])
        self.assertEqual(dots_in_cell(html, "docs"), [])

    def test_a_thread_without_touched_paths_has_no_dot(self):
        html = app([thread("t")])
        self.assertNotIn('class="dot', html)

    def test_dots_in_one_cell_are_in_tag_order(self):
        html = app([
            thread("r", created=90, touched=["docs/a"]),
            thread("k2", "r", created=10, touched=["docs/b"]),
            thread("k1", "r", created=30, touched=["docs/c"]),
        ])
        self.assertEqual(dots_in_cell(html, "docs"), ["A", "A1", "A2"])

    def test_an_idle_threads_dot_is_dimmed(self):
        html = app([thread("t", state="idle", touched=["docs/a"])])
        self.assertRegex(html, r'class="dot[^"]*\bidle\b')

    def test_older_touched_cells_show_a_faint_trail_mark(self):
        html = app([thread("t", touched=["src/a/x", "docs/y", "src/a/z"])])
        m = re.search(r'data-cell="docs".*?</div><!--/cell-->', html, re.S).group(0)
        self.assertIn('class="trail"', m)


class CardTest(unittest.TestCase):
    def test_all_text_from_agents_is_escaped(self):
        html = app([thread("t", title="<script>x</script>", now="Editing <b>",
                           thought="a & b", last_said='"q"')],
                   {"t": Plan("<i>goal</i>", "<s>", (Item("1", "<u>", "todo", ()),), NOW)})
        self.assertNotIn("<script>x", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn("<i>goal", html)
        self.assertNotIn("<u>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_card_shows_the_now_line_thought_and_model(self):
        c = card(app([thread("t", now="Editing src/a/x.py", thought="The cap goes first.",
                             model="claude-opus-5-5")]), "A")
        self.assertIn("Editing src/a/x.py", c)
        self.assertIn("The cap goes first.", c)
        self.assertIn("claude-opus-5-5", c)

    def test_an_idle_card_shows_what_the_thread_said_last(self):
        c = card(app([thread("t", state="idle", last_said="All 14 mutants died.")]), "A")
        self.assertIn("All 14 mutants died.", c)

    def test_a_thread_that_needs_the_user_is_marked(self):
        c = card(app([thread("t", state="needs_you")]), "A")
        self.assertIn("needs-you", c)
        self.assertIn("needs you", c)

    def test_an_idle_child_quiet_for_over_ten_minutes_is_one_line(self):
        html = app([thread("r"), thread("old", "r", state="idle", created=50, updated=11),
                    thread("new", "r", state="idle", created=40, updated=9)])
        self.assertIn("compact", card(html, "A1").split(">")[0])
        self.assertNotIn("compact", card(html, "A2").split(">")[0])

    def test_an_idle_root_is_never_compact(self):
        self.assertNotIn("compact", card(app([thread("r", state="idle", updated=60)]), "A").split(">")[0])

    def test_the_agent_status_line_shows_on_the_card(self):
        c = card(app([thread("t")], {"t": Plan(None, "Waiting on the timed take", (), NOW)}), "A")
        self.assertIn("Waiting on the timed take", c)


class PlanTest(unittest.TestCase):
    def plan_html(self, items, goal="Cap lights"):
        return card(app([thread("t")], {"t": Plan(goal, None, tuple(items), NOW)}), "A")

    def test_the_goal_shows_on_the_card(self):
        self.assertIn("Cap lights", self.plan_html([]))

    def test_a_done_top_item_collapses_to_one_line_with_a_count(self):
        c = self.plan_html([Item("1", "Diagnose", "done", (
            Item("1.1", "Count lights", "done", ()), Item("1.2", "Profile", "done", ())))])
        self.assertIn("Diagnose", c)
        self.assertIn("(2/2)", c)
        self.assertNotIn("Count lights", c)

    def test_an_open_top_item_shows_its_children_and_a_count(self):
        c = self.plan_html([Item("2", "Prototype", "doing", (
            Item("2.1", "Cap in cull", "done", ()), Item("2.2", "Timed take", "doing", ()),
            Item("2.3", "Mutation pass", "todo", ())))])
        self.assertIn("(1/3)", c)
        for text in ("Cap in cull", "Timed take", "Mutation pass"):
            self.assertIn(text, c)

    def test_each_item_shows_its_state(self):
        c = self.plan_html([Item("1", "a", "done", ()), Item("2", "b", "doing", ()),
                            Item("3", "c", "todo", ())])
        self.assertRegex(c, r'class="item done"[^>]*>.*?a<')
        self.assertRegex(c, r'class="item doing"[^>]*>.*?b<')
        self.assertRegex(c, r'class="item todo"[^>]*>.*?c<')


class HeaderTest(unittest.TestCase):
    def test_the_header_counts_threads_by_state(self):
        html = app([thread("a"), thread("b", state="needs_you"), thread("c", state="idle"),
                    thread("d", state="working")])
        self.assertIn("2 working", html)
        self.assertIn("1 needs you", html)
        self.assertIn("1 idle", html)


class AgeTest(unittest.TestCase):
    def test_ages_use_the_largest_whole_unit(self):
        self.assertEqual(format_age(timedelta(seconds=30)), "now")
        self.assertEqual(format_age(timedelta(minutes=12, seconds=59)), "12m")
        self.assertEqual(format_age(timedelta(hours=3, minutes=59)), "3h")
        self.assertEqual(format_age(timedelta(days=2, hours=5)), "2d")

    def test_a_negative_age_is_now(self):
        self.assertEqual(format_age(timedelta(seconds=-5)), "now")


class EdgeTest(unittest.TestCase):
    def test_a_project_without_files_says_so_on_the_map(self):
        self.assertIn("No files to map.", app([thread("t")], cells=[]))

    def test_an_empty_window_says_so_in_the_tree(self):
        self.assertIn("No threads in the window.", app([]))
        self.assertIn("no threads", app([]))

    def test_a_plan_without_a_goal_still_shows_its_items(self):
        c = card(app([thread("t")], {"t": Plan(None, None, (Item("1", "only item", "todo", ()),), NOW)}), "A")
        self.assertIn("only item", c)
        self.assertNotIn('class="goal"', c)

    def test_roots_with_work_in_their_tree_come_first(self):
        html = app([thread("quiet", created=90, state="idle"),
                    thread("busy", created=10, state="idle"),
                    thread("kid", "busy", created=5, state="working")])
        self.assertLess(html.index('data-tag="B"'), html.index('data-tag="A"'))

    def test_root_tags_continue_after_z_with_two_letters(self):
        html = app([thread(f"r{i:02}", created=100 - i) for i in range(28)])
        self.assertIn("Thread r25", card(html, "Z"))
        self.assertIn("Thread r26", card(html, "AA"))
        self.assertIn("Thread r27", card(html, "AB"))

    def test_hues_repeat_after_the_palette_ends(self):
        html = app([thread(f"r{i}", created=100 - i) for i in range(9)])
        hue = lambda tag: re.search(r'--hue:\s*([^;"]+)', card(html, tag)).group(1)
        self.assertEqual(hue("A"), hue("I"))

    def test_a_cell_with_a_working_thread_is_marked_busy(self):
        html = app([thread("w", touched=["docs/a"]), thread("i", state="idle", touched=["src/a/x"])])
        self.assertRegex(html, r'class="cell d1 busy"[^>]*data-cell="docs"')
        self.assertRegex(html, r'class="cell d2"[^>]*data-cell="src/a"')

    def test_a_path_outside_the_map_gives_no_dot_but_the_next_path_does(self):
        html = app([thread("t", touched=["zzz/unknown", "docs/a"])], cells=CELLS[:1] + CELLS[3:])
        self.assertEqual(dots_in_cell(html, "docs"), ["A"])
        self.assertEqual(dots_in_cell(html, "src"), [])

    def test_a_compact_card_keeps_its_last_words_and_its_children(self):
        html = app([thread("r"), thread("old", "r", state="idle", updated=30, last_said="Done here."),
                    thread("g", "old")])
        self.assertIn("Done here.", card(html, "A1"))
        self.assertIn('data-tag="A1.1"', card(html, "A1"))

    def test_item_ids_are_unique_across_plans(self):
        plan = Plan(None, None, (Item("1", "x", "todo", ()),), NOW)
        html = app([thread("a"), thread("b")], {"a": plan, "b": plan})
        ids = re.findall(r'id="(i-[0-9a-f]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))


CSS = (pathlib.Path(__file__).parent.parent / "agentmap" / "static" / "app.css").read_text()
CSS_CLASSES = set(re.findall(r"\.([a-z][a-z0-9-]*)", CSS))
VOID = {"meta", "link", "br", "img", "input"}
# Elements whose direct text must be blank: their content is other elements.
STRUCTURAL = {"html", "head", "body", "main", "section", "article", "ol", "header"}
STRUCTURAL_DIVS = {"map-box", "pins", "kids", "plan"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.classes = [], [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.classes.update((a.get("class") or "").split())
        if tag not in VOID:
            self.stack.append((tag, a.get("class") or ""))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"unbalanced </{tag}> in {self.stack[-3:]}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if not self.stack and data.strip():
            self.errors.append(f"stray text {data.strip()!r} outside the root element")
        if self.stack and data.strip():
            tag, cls = self.stack[-1]
            if tag in STRUCTURAL or (tag == "div" and cls in STRUCTURAL_DIVS):
                self.errors.append(f"stray text {data.strip()!r} in <{tag} class={cls!r}>")


def check(html):
    c = _Checker()
    c.feed(html)
    c.close()
    if c.stack:
        c.errors.append(f"unclosed {c.stack}")
    return c


FULL_SCENE = dict(
    threads=[
        thread("r", created=90, now="Waiting on background work", thought="Next, merge.",
               last_said="Tests pass.", touched=["src/a/x", "docs/y", "src/a/z"]),
        thread("k1", "r", created=50, state="needs_you", touched=["src/b/q"]),
        thread("k2", "r", created=40, state="idle", updated=30, last_said="Done."),
        thread("k3", "r", created=30, state="failed", updated=2),
        thread("g", "k1", created=20, state="idle", touched=["docs/z", "src/a/q"]),
        thread("r2", created=10, touched=["docs/w", "src/b/1", "src/a/2"]),
    ],
    plans={"r": Plan("Cap lights", "Merging", (
        Item("1", "Diagnose", "done", (Item("1.1", "Count", "done", ()),)),
        Item("2", "Prototype", "doing", (Item("2.1", "Cull", "done", ()),
                                        Item("2.2", "Take", "doing", ()),
                                        Item("2.3", "Mutants", "todo", ()))),
        Item("3", "Merge", "todo", ())), NOW)},
)


class MarkupTest(unittest.TestCase):
    """The markup is the contract between render.py and app.css."""

    def scene(self):
        return render_app(FULL_SCENE["threads"], FULL_SCENE["plans"], CELLS, NOW, "demo")

    def test_the_app_is_well_formed_with_no_stray_text(self):
        self.assertEqual(check(self.scene()).errors, [])
        self.assertEqual(check(app([])).errors, [])
        self.assertEqual(check(app([thread("t")], cells=[])).errors, [])

    def test_the_scene_has_shared_cells_so_that_joins_are_checked(self):
        html = self.scene()
        self.assertEqual(dots_in_cell(html, "docs"), ["A1.1", "B"])
        m = re.search(r'data-cell="src/a".*?</div><!--/cell-->', html, re.S).group(0)
        self.assertEqual(m.count('class="trail"'), 2)
        m = re.search(r'data-cell="docs".*?</div><!--/cell-->', html, re.S).group(0)
        self.assertEqual(m.count('class="trail"'), 1)

    def test_the_page_is_well_formed_with_no_stray_text(self):
        self.assertEqual(check(render_page(self.scene())).errors, [])

    def test_every_class_in_the_markup_has_a_style(self):
        missing = check(self.scene()).classes - CSS_CLASSES
        self.assertEqual(missing, set())

    def test_the_page_links_the_style_sheet_and_has_a_title(self):
        page = render_page("")
        self.assertIn('<link rel="stylesheet" href="/static/app.css">', page)
        self.assertIn("<title>agentmap</title>", page)

    def test_element_ids_are_unique(self):
        ids = re.findall(r' id="([^"]+)"', self.scene())
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_dot_has_the_hue_of_its_card(self):
        html = self.scene()
        card_hue = re.search(r'data-tag="A" style="--hue:(\d+)"', html).group(1)
        dot_hue = re.search(r'class="dot [a-z-]+" style="--hue:(\d+)">A<', html).group(1)
        self.assertEqual(card_hue, dot_hue)


class DetailTest(unittest.TestCase):
    def test_age_limits_fall_into_the_larger_unit(self):
        self.assertEqual(format_age(timedelta(seconds=59)), "now")
        self.assertEqual(format_age(timedelta(seconds=60)), "1m")
        self.assertEqual(format_age(timedelta(seconds=3599)), "59m")
        self.assertEqual(format_age(timedelta(seconds=3600)), "1h")
        self.assertEqual(format_age(timedelta(seconds=86399)), "23h")
        self.assertEqual(format_age(timedelta(seconds=86400)), "1d")
        self.assertEqual(format_age(timedelta(seconds=2 * 86400 - 1)), "1d")

    def test_a_child_idle_exactly_ten_minutes_is_not_yet_compact(self):
        html = app([thread("r"), thread("k", "r", state="idle", updated=10)])
        self.assertNotIn("compact", card(html, "A1").split(">")[0])

    def test_a_thread_whose_parent_is_not_in_the_snapshot_is_a_root(self):
        html = app([thread("orphan", "gone")])
        self.assertIn("Thread orphan", card(html, "A"))

    def test_a_root_that_needs_the_user_comes_first(self):
        html = app([thread("quiet", created=90, state="idle"),
                    thread("asks", created=10, state="needs_you")])
        self.assertLess(html.index('data-tag="B"'), html.index('data-tag="A"'))

    def test_a_cell_with_a_thread_that_needs_the_user_is_busy(self):
        html = app([thread("t", state="needs_you", touched=["docs/a"])])
        self.assertRegex(html, r'class="cell d1 busy"[^>]*data-cell="docs"')

    def test_the_header_counts_failed_threads_and_says_when_empty(self):
        self.assertIn('<span class="n failed">1 failed</span>', app([thread("f", state="failed")]))
        self.assertIn('<div class="counts">no threads</div>', app([]))
        self.assertIn('<div class="counts"><span class="n working">1 working</span> '
                      '<span class="n idle">1 idle</span></div>',
                      app([thread("w"), thread("i", state="idle")]))

    def test_the_header_shows_the_local_clock_time(self):
        self.assertIn(f"<time>{NOW.astimezone().strftime('%H:%M')}</time>", app([]))
        self.assertRegex(app([]), r"<time>\d\d:\d\d</time>")

    def test_the_empty_tree_message_is_plain(self):
        self.assertIn('<section id="tree"><p class="empty">No threads in the window.</p></section>', app([]))

    def test_state_labels_name_the_state_and_its_age(self):
        html = app([thread("w", created=90), thread("n", created=80, state="needs_you"),
                    thread("i", created=70, state="idle", updated=12),
                    thread("f", created=60, state="failed", updated=3)])
        self.assertIn('<span class="state">working</span>', html)
        self.assertIn('<span class="state">needs you</span>', html)
        self.assertIn('<span class="state">idle 12m</span>', html)
        self.assertIn('<span class="state">failed 3m</span>', html)

    def test_a_full_card_shows_every_part_in_order(self):
        c = card(app([thread("t", created=125, now="Editing x", thought="Hm.", last_said="Done.")],
                     {"t": Plan("G", "S", (Item("1", "I", "todo", ()),), NOW)}), "A")
        parts = ['<div class="meta">claude · claude-opus-5-5 · started 2h ago</div>',
                 '<div class="status">◆ S</div>', '<div class="now">▸ Editing x</div>',
                 '<div class="thought">Hm.</div>', '<div class="said">↳ Done.</div>',
                 '<div class="goal">G</div>']
        positions = [c.index(p) for p in parts]
        self.assertEqual(positions, sorted(positions))

    def test_a_model_less_thread_shows_only_its_provider(self):
        c = card(app([thread("t", model=None, provider="codex")]), "A")
        self.assertIn('<div class="meta">codex · started 1h ago</div>', c)

    def test_a_compact_card_without_last_words_has_only_its_header(self):
        html = app([thread("r"), thread("k", "r", state="idle", updated=30)])
        self.assertRegex(card(html, "A1"), r"^<article[^>]*><header>.*</header></article>$")

    def test_each_older_cell_gets_one_trail_mark_per_thread(self):
        html = app([thread("t", touched=["src/b/1", "docs/a", "docs/b", "docs/c"])])
        m = re.search(r'data-cell="docs".*?</div><!--/cell-->', html, re.S).group(0)
        self.assertEqual(m.count('class="trail"'), 1)

    def test_cell_tops_scale_with_the_map_height(self):
        html = app([], cells=[Cell("docs", "docs", 1, 0, MAP_H / 2, MAP_W, MAP_H / 2, 1)])
        self.assertIn("top:50.000%", html)

    def test_a_leaf_item_has_no_child_list(self):
        c = card(app([thread("t")], {"t": Plan(None, None, (Item("1", "leaf", "todo", ()),), NOW)}), "A")
        self.assertRegex(c, r'<li class="item todo" id="i-[0-9a-f]+"><span class="g">○</span>'
                            r'<span class="t">leaf</span></li>')

    def test_an_open_parent_lists_its_children_in_a_nested_list(self):
        c = card(app([thread("t")], {"t": Plan(None, None, (Item("1", "p", "doing", (
            Item("1.1", "a", "done", ()), Item("1.2", "b", "todo", ()))),), NOW)}), "A")
        self.assertRegex(c, r'<span class="count">\(1/2\)</span><ol class="items"><li class="item done"'
                            r'[^>]*>.*?</li><li class="item todo"[^>]*>.*?</li></ol></li></ol>')


class HoverLinkTest(unittest.TestCase):
    """Hovering a dot highlights its card, and hovering a card highlights its dot.

    One frontend signal, $hover, holds the tag under the pointer. Datastar
    keeps it in the browser; the server never sees it.
    """

    def test_the_page_declares_the_hover_signal_outside_the_morphed_app(self):
        page = render_page('<main id="app"></main>')
        body = re.search(r"<body[^>]*>", page).group(0)
        self.assertIn("data-signals:hover=\"''\"", body)

    def test_a_dot_sets_the_signal_to_its_tag_while_the_pointer_is_on_it(self):
        html = app([thread("r", created=90), thread("k", "r", touched=["docs/a"])])
        dot = re.search(r'<span[^>]*class="dot[^"]*"[^>]*>A1<', html).group(0)
        self.assertIn('data-on:mouseenter="$hover = \'A1\'"', dot)
        self.assertIn('data-on:mouseleave="$hover = \'\'"', dot)

    def test_a_card_sets_the_signal_to_its_own_tag_not_its_parents(self):
        html = app([thread("r", created=90), thread("k", "r")])
        opening = card(html, "A1").split(">")[0]
        self.assertIn('data-on:mouseover__stop="$hover = \'A1\'"', opening)
        self.assertIn('data-on:mouseleave="$hover = \'\'"', opening)

    def test_a_dot_and_its_card_light_up_on_the_same_signal_value(self):
        html = app([thread("r", touched=["docs/a"])])
        dot = re.search(r'<span[^>]*class="dot[^"]*"[^>]*>A<', html).group(0)
        opening = card(html, "A").split(">")[0]
        for element in (dot, opening):
            self.assertIn('data-class:hl="$hover == \'A\'"', element)

    def test_the_highlight_class_has_a_style_for_dots_and_cards(self):
        self.assertIn(".dot.hl", CSS)
        self.assertIn(".card.hl", CSS)


if __name__ == "__main__":
    unittest.main()
