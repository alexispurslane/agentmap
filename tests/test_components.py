"""Behavior of the dashboard's HTML components."""

import pathlib
import re
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from agentmap.components import app, page
from agentmap.components._template import templates
from agentmap.components.fade.fade import fade
from agentmap.components.hover.hover import hover
from agentmap.components.project_map.project_map import MAP_H, MAP_W
from agentmap.components.tag.tag import tag
from agentmap.components.view import Forest, age_class, element_id, format_age, summary, title_without
from agentmap.events import Event
from agentmap.status import Status
from agentmap.t3 import AgentThread, Touch
from agentmap.treemap import Cell

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


def ago(minutes):
    return NOW - timedelta(minutes=minutes)


def fades(minutes):
    """The fade attribute of a mark made `minutes` before NOW."""
    return fade(ago(minutes))


def thread(id, parent=None, *, title=None, created=60, updated=0, state="working",
           now=None, thought=None, thought_at=None, last_said=None, said_at=None,
           touched=(), provider="claude", model="claude-opus-5-5", delegation=None,
           settled_at=None):
    """A thread created `created` and updated `updated` minutes before NOW.

    `touched` holds paths (touched 1 minute apart, newest first) or (path, minutes) pairs.
    """
    touches = tuple(Touch(p[0], ago(p[1])) if isinstance(p, tuple) else Touch(p, ago(i))
                    for i, p in enumerate(touched))
    return AgentThread(
        id=id, parent_id=parent, title=title or f"Thread {id}", provider=provider,
        model=model, state=state, created_at=ago(created), updated_at=ago(updated),
        now=now, thought=thought, thought_at=None if thought_at is None else ago(thought_at),
        last_said=last_said, said_at=None if said_at is None else ago(said_at), touched=touches,
        delegation=delegation, settled_at=None if settled_at is None else ago(settled_at))


def status(text, minutes=0):
    return Status(text, ago(minutes))


def event(thread_id, kind, minutes, text=""):
    return Event(ago(minutes), thread_id, kind, text)


CELLS = [
    Cell("src", "src", 1, 0, 0, 600, 600, 100),
    Cell("src/a", "a", 2, 0, 16, 300, 584, 50),
    Cell("src/b", "b", 2, 300, 16, 300, 584, 50),
    Cell("docs", "docs", 1, 600, 0, 480, 600, 40),
]


def render(threads, statuses=None, cells=CELLS, events=(), tickets=frozenset()):
    return app(threads, statuses or {}, cells, list(events), NOW, "demo", tickets)


def row(html, tag):
    """The opening tag and own content of the row with this tag, up to its first child row."""
    m = re.search(rf'<article class="row[^"]*"[^>]*data-tag="{re.escape(tag)}".*?(?=<article|</article>)',
                  html, re.S)
    assert m, f"no row with tag {tag}"
    return m.group(0)


def opening(html, tag):
    return row(html, tag).split(">", 1)[0]


def hue_of(html, tag):
    return float(re.search(r'--hue:([0-9.]+)', opening(html, tag)).group(1))


def section(html, id):
    m = re.search(rf'<section id="{id}".*?</section>', html, re.S)
    assert m, f"no section {id}"
    return m.group(0)


def dots(html):
    """(tag, x%, y%) of every dot on the map."""
    return [(m.group(3), float(m.group(1)), float(m.group(2)))
            for m in re.finditer(r'class="dot [^"]*" style="--hue:[0-9.]+;left:([0-9.]+)%;'
                                 r'top:([0-9.]+)%">([^<]+)<', html)]


def dot_tags_in(html, key):
    """The tags of the dots that sit inside the cell `key`."""
    cell = re.search(rf'data-cell="{re.escape(key)}" style="left:([0-9.]+)%;top:([0-9.]+)%;'
                     r'width:([0-9.]+)%;height:([0-9.]+)%"', html)
    x, y, w, h = map(float, cell.groups())
    return [t for t, dx, dy in dots(html) if x <= dx <= x + w and y <= dy <= y + h]


def log_entries(html):
    """(time, tag, text) of each event log entry, top to bottom."""
    out = []
    for m in re.finditer(r'<div class="ev ([a-z]+) (a[0-4])".*?<time>([^<]+)</time>'
                         r'<span class="tag"[^>]*>([^<]+)</span><span class="what">(.*?)</span></div>',
                         section(html, "log"), re.S):
        out.append((m.group(1), m.group(2), m.group(4), re.sub(r"<[^>]+>", "", m.group(5)).strip()))
    return out


CSS = (pathlib.Path(__file__).parent.parent / "agentmap" / "static" / "app.css").read_text()
CSS_CLASSES = set(re.findall(r"\.([a-z][a-z0-9-]*)", CSS))
# State classes that keep the default look on purpose: a plain verb in the log.
UNSTYLED = {"started", "edits", "status"}
VOID = {"meta", "link", "br", "img", "input", "path", "circle"}
STRUCTURAL = {"html", "head", "body", "main", "section", "article", "ol", "header", "nav", "svg", "g"}
STRUCTURAL_DIVS = {"map-box", "kids", "scroll"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.classes = [], [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.classes.update((a.get("class") or "").split())
        if tag not in VOID:
            self.stack.append((tag, a.get("class") or ""))

    def handle_startendtag(self, tag, attrs):
        self.classes.update((dict(attrs).get("class") or "").split())

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"unbalanced </{tag}> in {self.stack[-3:]}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if not data.strip():
            return
        if not self.stack:
            self.errors.append(f"stray text {data.strip()!r} outside the root element")
            return
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


SCENE = dict(
    threads=[
        thread("r", created=90, now="Waiting on background work", thought="Next, merge.",
               thought_at=1, last_said="Tests pass.", said_at=3,
               touched=["src/a/x", "docs/y", "src/a/z"]),
        thread("k1", "r", created=50, state="needs_you", touched=["src/b/q"]),
        thread("k2", "r", created=40, state="idle", updated=30, last_said="Done.", said_at=30),
        thread("k3", "r", created=30, state="failed", updated=2),
        thread("g", "k1", created=20, state="idle", touched=["docs/z", "src/a/q"]),
        thread("r2", created=10, touched=["docs/w", "src/b/1", "src/a/2"]),
    ],
    statuses={"r": status("Merging", 0)},
    events=[event("r", "status", 50, "Cap lights"), event("k1", "asks", 0.5, "Keep the old sort?"),
            event("r2", "edits", 2, "docs/w"), event("r2", "edits", 3, "src/b/1"),
            event("k2", "finished", 30, "Done."), event("gone", "started", 1, "Not in snapshot")],
)


def scene():
    return render(SCENE["threads"], SCENE["statuses"], events=SCENE["events"])


class TemplateTest(unittest.TestCase):
    def test_every_component_folder_has_a_function_file_and_a_template(self):
        root = pathlib.Path(__file__).parent.parent / "agentmap" / "components"
        folders = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith("_")]
        self.assertGreaterEqual(len(folders), 9)
        for d in folders:
            self.assertTrue((d / f"{d.name}.py").is_file(), d.name)
            self.assertTrue((d / f"{d.name}.html").is_file(), d.name)
            self.assertIn(d.name, templates())

    def test_values_are_escaped_and_templates_keep_double_quoted_attributes(self):
        self.assertEqual(tag("<A>", 1.0), '<span class="tag" style="--hue:1.0">&lt;A&gt;</span>')
        for name, text in templates().items():
            # A quote after `name=` is an attribute quote. `$hover = '{{tag}}'` is a
            # JavaScript string inside a double-quoted attribute, which is fine.
            self.assertNotRegex(text, r"[\w:-]='", name)

    def test_a_thread_mark_focuses_its_thread_and_lights_while_it_is_in_focus(self):
        self.assertEqual(hover("A1", 212.5, "mouseenter"),
                         "data-on:mouseenter=\"$focus = ' A1 '; $hue = '212.5'\" data-on:mouseleave=\"$focus = ''\" "
                         "data-class:hl=\"$focus.includes(' A1 ')\"")


class FadeTest(unittest.TestCase):
    def test_a_fade_follows_the_clock_signal_from_the_marks_time(self):
        self.assertEqual(fade(datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)),
                         'data-style:--fade="Math.min(1, Math.sqrt(Math.max(0, $now - 1794668400) / 3600))"')

    def test_a_mark_without_a_time_is_fully_faded(self):
        self.assertIn("$now - 0)", fade(None))

    def test_the_page_ticks_the_clock_signal_every_ten_seconds(self):
        body = re.search(r"<body[^>]*>", page("", "", "")).group(0)
        self.assertIn("now: Date.now() / 1000", body)
        self.assertIn("setInterval(() =&gt; $now = Date.now() / 1000, 10000)", body)

    def test_the_fade_is_a_registered_number_that_moves_smoothly(self):
        self.assertIn("@property --fade { syntax: '<number>'; inherits: true; initial-value: 0; }", CSS)
        # A transition would start every mark at --fade 0 on page load.
        self.assertNotIn("transition: --fade", CSS)


class TicketBadgeTest(unittest.TestCase):
    IDS = frozenset({"ter-icn3", "t7-3e22", "t0-6gej"})

    def test_a_row_shows_a_badge_for_each_ticket_that_its_title_or_status_names(self):
        html = render([thread("a", title="ter-icn3: Unity research")],
                      {"a": status("t7-3e22: step two")}, tickets=self.IDS)
        o = row(html, "A")
        self.assertRegex(o, r'<span class="tkbs"><button class="tkb" [^>]*>ter-icn3</button>'
                            r'<button class="tkb" [^>]*>t7-3e22</button></span><span class="state">')

    def test_the_title_drops_the_ids_that_are_badges(self):
        for title, shown in [("ter-icn3: Unity research", "Unity research"),
                             ("Mottle and specks (t7-3e22)", "Mottle and specks"),
                             ("Build t0-6gej area record", "Build area record"),
                             ("t0-6gej, t7-3e22 - merge both", "merge both"),
                             ("[ter-icn3] Budget", "Budget"),
                             ("ter-icn3", "ter-icn3"),
                             ("Keep ter-zzzz in the title", "Keep ter-zzzz in the title")]:
            with self.subTest(title=title):
                self.assertEqual(title_without(title, self.IDS), shown)

    def test_a_badge_is_a_ticket_mark_with_its_own_place(self):
        dom = element_id("t", "ter-icn3")
        o = row(render([thread("a", title="ter-icn3: Unity research")], tickets=self.IDS), "A")
        self.assertIn("$focus = ' A '; $hue = '75.0'; $pin || $peek == '" + dom + "'", o)
        self.assertIn(f"$pinat = 'b-A-{dom}', @get('/pin/ter-icn3')", o)

    def test_a_row_without_tickets_keeps_an_empty_badge_slot(self):
        self.assertIn('<span class="tkbs"></span><span class="state">', row(render([thread("a")]), "A"))

    def test_a_badge_lights_and_anchors_like_the_other_ticket_marks(self):
        self.assertIn(".now-item:hover, .tk-head:hover, .tkb:hover { anchor-name: --peek; }", CSS)
        self.assertIn(".now-item.pinned, .tk-head.pinned, .tkb.pinned { anchor-name: --pin; }", CSS)
        self.assertRegex(CSS, r"\.tkb\.hl \{[^}]*var\(--focus-hue\)")


class PageTest(unittest.TestCase):
    def test_the_page_opens_the_stream_and_declares_the_hover_signal_on_body(self):
        html = page('<main id="app"></main>', "", "")
        body = re.search(r"<body[^>]*>", html).group(0)
        self.assertIn("data-signals=\"{focus: '', hue: '', epic: '', peek: '', pin: '', pinat: '', stale: false, now: Date.now() / 1000}\"", body)
        self.assertIn('data-style:--focus-hue="$hue"', body)
        self.assertIn("data-init=\"@get('/stream', {openWhenHidden: true});", body)
        self.assertIn('src="/static/datastar.js"', html)
        self.assertIn('<link rel="stylesheet" href="/static/app.css">', html)
        self.assertIn('<main id="app"></main>', html)

    def test_the_app_is_one_main_element_with_its_four_parts_in_order(self):
        html = scene()
        self.assertTrue(html.startswith('<main id="app">'))
        self.assertTrue(html.endswith("</main>"))
        self.assertNotIn('id="plans"', html)
        order = [html.index(x) for x in ('<nav id="rail"', '<section id="map"', '<section id="tree"',
                                         '<section id="log"')]
        self.assertEqual(order, sorted(order))


class MarkupTest(unittest.TestCase):
    def test_the_app_and_page_are_well_formed_with_no_stray_text(self):
        for html in (scene(), render([]), render([thread("t")], cells=[]), page(scene(), "", "")):
            self.assertEqual(check(html).errors, [])

    def test_every_class_in_the_markup_has_a_style(self):
        classes = check(scene()).classes | check(render([])).classes
        self.assertEqual(classes - CSS_CLASSES - UNSTYLED, set())
        self.assertEqual(UNSTYLED & CSS_CLASSES, set())

    def test_element_ids_are_unique(self):
        ids = re.findall(r' id="([^"]+)"', scene())
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_text_from_agents_is_escaped(self):
        html = render([thread("t", title="<script>x</script>", now="Editing <blink>",
                              thought="a & b", thought_at=0)],
                      {"t": status("<i>goal</i>", 0)},
                      events=[event("t", "asks", 0, "<em>?</em>")])
        for raw in ("<script>x", "<blink>", "<i>goal", "<em>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;", html)


class CarriedOverTest(unittest.TestCase):
    """Behaviors of the first renderer that the components keep."""

    def test_a_dot_has_the_hue_of_its_row(self):
        html = render([thread("a", created=90), thread("b", created=80, touched=["docs/x"])])
        dot_hue = float(re.search(r'class="dot [^"]*" style="--hue:([0-9.]+)', html).group(1))
        self.assertEqual(dot_hue, hue_of(html, "B"))

    def test_a_path_outside_the_map_gives_no_dot_but_the_next_path_does(self):
        html = render([thread("t", touched=["outside/x", "docs/y"])])
        self.assertEqual(dot_tags_in(html, "docs"), ["A"])

    def test_a_compact_row_keeps_its_children(self):
        html = render([thread("r"), thread("k", "r", state="idle", updated=30),
                       thread("g", "k", created=5)])
        self.assertIn("compact", opening(html, "A1"))
        self.assertIn("Thread g", row(html, "A1.1"))

    def test_a_full_row_shows_its_parts_in_order(self):
        r = row(render([thread("t", now="Running tests", last_said="Fixed it.", said_at=0)]), "A")
        parts = ['class="tag"', 'class="title"', 'class="state"', 'class="line tool"',
                 'class="line said', 'class="meta"']
        places = [r.index(x) for x in parts]
        self.assertEqual(places, sorted(places))

    def test_the_page_has_a_title(self):
        self.assertIn("<title>agentmap</title>", page("", "", ""))

    def test_the_highlight_class_has_a_style_for_each_linked_mark(self):
        for selector in (".dot.hl", ".row.hl", ".ev.hl"):
            self.assertIn(selector, CSS)


class TagAndHueTest(unittest.TestCase):
    def test_root_threads_get_letters_in_order_of_creation(self):
        html = render([thread("late", created=10), thread("early", created=90)])
        self.assertIn("Thread early", row(html, "A"))
        self.assertIn("Thread late", row(html, "B"))

    def test_children_get_the_parent_tag_and_their_position(self):
        html = render([thread("root", created=90), thread("c2", "root", created=20),
                       thread("c1", "root", created=40), thread("g1", "c2", created=10)])
        self.assertIn("Thread c1", row(html, "A1"))
        self.assertIn("Thread c2", row(html, "A2"))
        self.assertIn("Thread g1", row(html, "A2.1"))

    def test_root_tags_continue_after_z_with_two_letters(self):
        html = render([thread(f"r{i:02}", created=100 - i) for i in range(28)])
        self.assertIn("Thread r26", row(html, "AA"))

    def test_every_thread_has_its_own_hue_far_from_the_others(self):
        html = render([thread(f"t{i}", created=100 - i) for i in range(8)])
        hues = [hue_of(html, t) for t in "ABCDEFGH"]
        gaps = [min(abs(a - b), 360 - abs(a - b)) for i, a in enumerate(hues) for b in hues[i + 1:]]
        self.assertGreaterEqual(min(gaps), 30)

    def test_a_new_thread_keeps_the_hues_of_older_threads(self):
        before = render([thread("r", created=90), thread("k", "r", created=80)])
        after = render([thread("r", created=90), thread("k", "r", created=80),
                        thread("new", "r", created=1)])
        self.assertEqual([hue_of(before, t) for t in ("A", "A1")],
                         [hue_of(after, t) for t in ("A", "A1")])


class AgeTest(unittest.TestCase):
    def test_the_ink_ramp_has_five_steps_at_1_5_15_and_60_minutes(self):
        steps = [age_class(NOW, NOW - timedelta(seconds=s))
                 for s in (0, 59, 60, 299, 300, 899, 900, 3599, 3600, 10**6)]
        self.assertEqual(steps, ["a0", "a0", "a1", "a1", "a2", "a2", "a3", "a3", "a4", "a4"])

    def test_a_mark_without_a_time_is_inked_as_old(self):
        self.assertEqual(age_class(NOW, None), "a4")

    def test_ages_use_the_largest_whole_unit(self):
        self.assertEqual([format_age(timedelta(seconds=s)) for s in (-5, 59, 60, 3599, 3600, 86399, 86400)],
                         ["now", "now", "1m", "59m", "1h", "23h", "1d"])


class SummaryTest(unittest.TestCase):
    def test_the_summary_is_the_newest_of_status_message_and_thought(self):
        t = thread("t", thought="Thinking.", thought_at=1, last_said="Said.", said_at=5)
        self.assertEqual(summary(t, status("Posted.", 9)).kind, "thought")
        self.assertEqual(summary(t, status("Posted.", 0)).kind, "status")
        t = thread("t", thought="Thinking.", thought_at=8, last_said="Said.", said_at=5)
        self.assertEqual(summary(t, None).text, "Said.")

    def test_on_a_tie_the_posted_status_wins_then_the_message(self):
        t = thread("t", thought="Thinking.", thought_at=2, last_said="Said.", said_at=2)
        self.assertEqual(summary(t, status("Posted.", 2)).kind, "status")
        self.assertEqual(summary(t, None).kind, "said")

    def test_a_source_without_a_time_loses_to_any_source_with_one(self):
        t = thread("t", thought="Thinking.", thought_at=50, last_said="Said.")
        self.assertEqual(summary(t, None).kind, "thought")

    def test_a_thread_with_nothing_to_say_has_no_summary(self):
        self.assertIsNone(summary(thread("t"), None))


class AgentRowTest(unittest.TestCase):
    def test_a_working_row_shows_its_tool_call_and_its_newest_summary(self):
        r = row(render([thread("t", now="Editing src/a/x.py", thought="The cap goes first.",
                               thought_at=0, last_said="Old news.", said_at=20)]), "A")
        self.assertRegex(r, r'<div class="line tool"><span class="lbl">tool</span>'
                            r'<span class="text">Editing src/a/x.py</span></div>')
        self.assertRegex(r, r'<div class="line thought" data-style:--fade="[^"]+"><span class="lbl">thought</span>'
                            r'<span class="text">The cap goes first.</span></div>')
        self.assertNotIn("Old news.", r)

    def test_the_summary_line_fades_by_its_own_time(self):
        r = row(render([thread("t", last_said="Said.", said_at=20)]), "A")
        self.assertIn(f'<div class="line said" {fades(20)}>', r)

    def test_an_idle_row_has_no_tool_line(self):
        self.assertNotIn("line tool", row(render([thread("t", state="idle")]), "A"))

    def test_a_row_shows_provider_model_and_start(self):
        self.assertIn('<div class="meta">claude · claude-opus-5-5 · started 2h ago</div>',
                      row(render([thread("t", created=125)]), "A"))
        self.assertIn('<div class="meta">codex · started 1h ago</div>',
                      row(render([thread("t", model=None, provider="codex")]), "A"))

    def test_state_labels_name_the_state_and_its_age(self):
        html = render([thread("w", created=90), thread("n", created=80, state="needs_you"),
                       thread("i", created=70, state="idle", updated=12),
                       thread("f", created=60, state="failed", updated=3)])
        for label in ("working", "needs you", "idle 12m", "failed 3m"):
            self.assertIn(f'<span class="state">{label}</span>', html)

    def test_a_row_fades_by_the_threads_last_update(self):
        o = opening(render([thread("t", state="idle", updated=6)]), "A")
        self.assertTrue(o.startswith('<article class="row idle" '), o)
        self.assertIn(fades(6), o)

    def test_an_idle_child_quiet_for_over_ten_minutes_is_one_line(self):
        html = render([thread("r"), thread("old", "r", state="idle", created=50, updated=11,
                                           last_said="Hidden.", said_at=11),
                       thread("new", "r", state="idle", created=40, updated=10)])
        self.assertIn("compact", opening(html, "A1"))
        self.assertNotIn("Hidden.", row(html, "A1"))
        self.assertNotIn("compact", opening(html, "A2"))

    def test_an_idle_root_is_never_compact(self):
        self.assertNotIn("compact", opening(render([thread("r", state="idle", updated=60)]), "A"))

    def test_a_child_row_sits_inside_its_parent_row(self):
        html = render([thread("r"), thread("k", "r")])
        self.assertRegex(html, r'data-tag="A".*?<div class="kids"><article class="row[^"]*"[^>]*data-tag="A1"')

    def test_roots_with_work_in_their_tree_come_first(self):
        html = render([thread("quiet", created=90, state="idle"),
                       thread("busy", created=10, state="idle"),
                       thread("kid", "busy", created=5, state="needs_you")])
        self.assertLess(html.index('data-tag="B"'), html.index('data-tag="A"'))

    def test_the_tree_counts_its_threads_and_says_when_empty(self):
        self.assertIn("<small>1 thread</small>", section(render([thread("t")]), "tree"))
        self.assertIn("<small>2 threads</small>", section(render([thread("a"), thread("b")]), "tree"))
        self.assertIn('<p class="empty">No threads in the window.</p>', render([]))

    def test_a_row_links_to_its_dot_through_the_hover_signal(self):
        o = opening(render([thread("r", created=90), thread("k", "r")]), "A1")
        self.assertRegex(o, r"data-on:mouseover__stop=\"\$focus = ' A1 '; \$hue = '[0-9.]+'\"")
        self.assertIn("data-class:hl=\"$focus.includes(' A1 ')\"", o)


class MapTest(unittest.TestCase):
    def test_cells_are_placed_in_percent_of_the_map(self):
        html = render([], cells=[Cell("src", "src", 1, MAP_W / 4, MAP_H / 2, MAP_W / 2, MAP_H / 2, 1)])
        self.assertIn('data-cell="src" style="left:25.0%;top:50.0%;width:50.0%;height:50.0%"', html)

    def test_a_cell_of_files_directly_in_a_directory_is_labeled_files(self):
        html = render([], cells=[Cell("src/.", ".", 2, 0, 0, 100, 100, 1)])
        self.assertIn('<span class="label">files</span>', html)

    def test_a_project_without_files_says_so(self):
        self.assertIn('<p class="empty">No files to map.</p>', render([thread("t")], cells=[]))

    def test_a_threads_dot_sits_in_the_cell_of_its_newest_touched_file(self):
        html = render([thread("t", touched=["src/b/x.py", "docs/y.md"])])
        self.assertEqual(dot_tags_in(html, "src/b"), ["A"])
        self.assertEqual([t for t, *_ in dots(html)], ["A"])

    def test_a_thread_without_touched_files_has_no_dot_and_no_route(self):
        html = render([thread("t", touched=["outside/x"])])
        self.assertEqual(dots(html), [])
        self.assertNotIn("<g ", html)

    def test_threads_in_one_cell_get_separate_slots(self):
        html = render([thread(f"t{i}", created=50 - i, touched=["docs/a"]) for i in range(5)])
        places = [(x, y) for _, x, y in dots(html)]
        self.assertEqual(len(set(places)), 5)
        self.assertEqual(sorted(dot_tags_in(html, "docs")), ["A", "B", "C", "D", "E"])

    def test_the_route_runs_from_the_oldest_touch_to_the_newest_segment_at_the_dot(self):
        html = render([thread("t", touched=[("docs/a", 0), ("src/b/x", 7), ("src/a/y", 40)])])
        route = re.search(r'<g style="--hue:[0-9.]+"[^>]*>(.*?)</g>', html).group(1)
        segments = re.findall(r'<path class="(route[^"]*)" d="M([0-9.]+) ([0-9.]+)H([0-9.]+)V([0-9.]+)"', route)
        self.assertEqual([s[0] for s in segments], ["route", "route now"])
        # The first segment starts in src/a and the last one ends at the dot, in docs.
        self.assertLess(float(segments[0][1]), 300)
        (_, x, y), = dots(html)
        self.assertAlmostEqual(float(segments[-1][3]) / MAP_W * 100, x, places=1)
        self.assertAlmostEqual(float(segments[-1][4]) / MAP_H * 100, y, places=1)

    def test_older_segments_and_stops_fade_by_the_time_of_their_touch_the_newest_does_not(self):
        html = render([thread("t", touched=[("docs/a", 0), ("src/b/x", 7), ("src/a/y", 40)])])
        route = re.search(r'<g style="--hue:[0-9.]+"[^>]*>(.*?)</g>', html).group(1)
        self.assertRegex(route, rf'<path class="route" d="[^"]+" {re.escape(fades(7))}/>')
        self.assertRegex(route, r'<path class="route now" d="[^"]+"/>')
        self.assertEqual(re.findall(r'<circle class="stop" [^>]*?(data-style:--fade="[^"]*")/>', route),
                         [fades(40), fades(7)])

    def test_hovering_a_thread_lights_its_whole_route(self):
        html = render([thread("t", touched=["docs/a", "src/b/x"])])
        self.assertIn('<g style="--hue:75.0" data-class:hl="$focus.includes(\' A \')">', html)

    def test_a_thread_that_touched_one_cell_has_a_dot_and_no_route(self):
        html = render([thread("t", touched=["docs/a", "docs/b"])])
        self.assertEqual([t for t, *_ in dots(html)], ["A"])
        self.assertNotIn("<g ", html)

    def test_a_cell_touched_twice_appears_once_in_the_route(self):
        html = render([thread("t", touched=["docs/a", "src/a/x", "docs/b"])])
        self.assertEqual(len(re.findall(r'class="route ', html)), 1)

    def test_a_cell_with_working_or_asking_threads_is_busy(self):
        html = render([thread("w", touched=["docs/a"]), thread("i", state="idle", touched=["src/a/x"])])
        self.assertRegex(html, r'class="cell d1 busy"[^>]*data-cell="docs"')
        self.assertRegex(html, r'class="cell d2"[^>]*data-cell="src/a"')

    def test_a_dot_links_to_its_row_through_the_hover_signal(self):
        html = render([thread("t", touched=["docs/a"])])
        self.assertRegex(html, r"<span data-on:mouseenter=\"\$focus = ' A '; \$hue = '75.0'\"[^>]*class=\"dot working\"")

    def test_a_dot_carries_its_threads_state(self):
        html = render([thread("t", state="needs_you", touched=["docs/a"])])
        self.assertIn('class="dot needs-you"', html)


class EventLogTest(unittest.TestCase):
    def test_entries_are_newest_first_with_clock_tag_verb_and_ink(self):
        html = render([thread("a", created=90), thread("b", created=80)],
                      events=[event("a", "status", 20, "Ship it"), event("b", "asks", 0.5, "Merge now?"),
                              event("a", "finished", 3, "Cull pass")])
        self.assertEqual(log_entries(html), [("asks", "a0", "B", "asks Merge now?"),
                                             ("finished", "a1", "A", "finished Cull pass"),
                                             ("status", "a3", "A", "status Ship it")])
        self.assertIn(f"<time>{ago(0.5).astimezone().strftime('%H:%M')}</time>", html)

    def test_a_run_of_edits_by_one_thread_is_one_entry_with_a_count(self):
        html = render([thread("a", created=90), thread("b", created=80)],
                      events=[event("a", "edits", 1, "x.py"), event("a", "edits", 2, "y.py"),
                              event("a", "edits", 3, "x.py"), event("a", "edits", 4, "z.py"),
                              event("b", "edits", 5, "w.py"), event("a", "edits", 6, "v.py")])
        self.assertEqual([e[2:] for e in log_entries(html)],
                         [("A", "edits x.py and 2 more"), ("B", "edits w.py"), ("A", "edits v.py")])

    def test_events_of_threads_outside_the_snapshot_are_left_out(self):
        html = render([thread("a")], events=[event("gone", "started", 1, "Old")])
        self.assertIn('<p class="empty">No events in the window.</p>', html)

    def test_the_log_keeps_at_most_sixty_entries(self):
        html = render([thread("a")], events=[event("a", "status", i, f"g{i}") for i in range(70)])
        self.assertEqual(len(log_entries(html)), 60)

    def test_an_entry_links_to_its_thread_through_the_hover_signal(self):
        html = render([thread("a")], events=[event("a", "status", 1, "G")])
        self.assertIn("data-on:mouseenter=\"$focus = ' A '; $hue = '75.0'\"", section(html, "log"))


class RailTest(unittest.TestCase):
    def test_the_rail_counts_every_state_and_keeps_zero_counts_in_place(self):
        html = render([thread("a"), thread("b", state="needs_you"), thread("c", state="idle")])
        rail = re.search(r'<nav id="rail">.*?</nav>', html).group(0)
        self.assertIn('<h1>demo</h1>', rail)
        self.assertIn('<span class="count working"><b>1</b> working</span>', rail)
        self.assertIn('<span class="count needs-you"><b>1</b> needs you</span>', rail)
        self.assertIn('<span class="count idle"><b>1</b> idle</span>', rail)
        self.assertIn('<span class="count none"><b>0</b> failed</span>', rail)
        self.assertIn(f"<time>{NOW.astimezone().strftime('%H:%M')}</time>", rail)


class SettledTest(unittest.TestCase):
    """A subagent whose delegation ended leaves the board, but not the log."""

    def scene(self, kid_state="idle", delegation="completed", grandkid=None):
        threads = [thread("r", created=90),
                   thread("k", "r", created=50, state=kid_state, delegation=delegation,
                          touched=["src/b/x"]),
                   thread("k2", "r", created=40, delegation="running", touched=["docs/a"])]
        if grandkid:
            threads.append(thread("g", "k", created=20, state=grandkid, delegation="running"))
        return render(threads, events=[event("k", "finished", 0.5, "All done.")])

    def test_a_completed_subagent_leaves_the_map_and_the_tree(self):
        html = self.scene()
        self.assertNotIn('data-tag="A1"', section(html, "tree"))
        self.assertNotIn("A1", [t for t, *_ in dots(html)])

    def test_cancelled_and_interrupted_subagents_also_leave(self):
        for status in ("cancelled", "interrupted"):
            self.assertNotIn('data-tag="A1"', section(self.scene(delegation=status), "tree"), status)

    def test_a_settled_subagent_keeps_its_line_in_the_log(self):
        self.assertIn(("finished", "a0", "A1", "finished All done."), log_entries(self.scene()))

    def test_other_threads_keep_their_tags_and_hues_when_one_settles(self):
        live = self.scene(delegation="running")
        settled = self.scene()
        self.assertIn("Thread k2", row(settled, "A2"))
        self.assertEqual(hue_of(live, "A2"), hue_of(settled, "A2"))

    def test_a_resumed_subagent_comes_back_with_its_tag(self):
        self.assertIn("Thread k", row(self.scene(kid_state="working"), "A1"))
        self.assertIn("Thread k", row(self.scene(delegation="running"), "A1"))
        self.assertIn("Thread k", row(self.scene(kid_state="needs_you"), "A1"))

    def test_a_failed_subagent_stays(self):
        self.assertIn("Thread k", row(self.scene(kid_state="failed", delegation="failed"), "A1"))

    def test_a_settled_subagent_with_a_live_descendant_stays(self):
        html = self.scene(grandkid="working")
        self.assertIn("Thread k", row(html, "A1"))
        self.assertIn("Thread g", row(html, "A1.1"))

    def test_a_root_without_a_settle_mark_stays(self):
        self.assertIn("Thread r", row(render([thread("r", state="idle", updated=50)]), "A"))

    def test_a_root_that_t3_settled_leaves_the_map_and_the_tree(self):
        html = render([thread("r", state="idle", settled_at=1, touched=["src/b/x"]),
                       thread("s", created=30, touched=["docs/a"])])
        self.assertNotIn('data-tag="A"', section(html, "tree"))
        self.assertNotIn("A", [t for t, *_ in dots(html)])
        self.assertIn("Thread s", row(html, "B"))

    def test_a_failure_stays_although_t3_marks_it_settled(self):
        # T3 Code marks a subagent settled the moment it fails, so the mark alone hides failures.
        failed_task = render([thread("r", created=90),
                              thread("k", "r", created=50, state="idle", delegation="failed",
                                     settled_at=1)])
        self.assertIn("Thread k", row(failed_task, "A1"))
        failed_root = render([thread("r", state="failed", settled_at=1)])
        self.assertIn("Thread r", row(failed_root, "A"))

    def test_a_thread_that_t3_settled_comes_back_while_it_works_or_needs_you(self):
        for state in ("working", "needs_you"):
            html = render([thread("r", state=state, settled_at=1)])
            self.assertIn("Thread r", row(html, "A"), state)

    def test_a_root_that_t3_settled_stays_while_a_descendant_works(self):
        html = render([thread("r", state="idle", settled_at=1),
                       thread("k", "r", created=30, delegation="running")])
        self.assertIn("Thread r", row(html, "A"))

    def test_the_tree_counts_only_the_threads_it_shows(self):
        self.assertIn("<small>2 threads</small>", section(self.scene(), "tree"))


class ForestTest(unittest.TestCase):
    def test_a_thread_whose_parent_is_not_in_the_snapshot_is_a_root(self):
        self.assertEqual(Forest([thread("orphan", "gone")]).tag, {"orphan": "A"})


if __name__ == "__main__":
    unittest.main()
