import dataclasses
import itertools
import os
import pathlib
import subprocess
import tempfile
import unittest

from agentmap.treemap import Cell, build_cells, code_weights, locate

W, H = 1080.0, 600.0
EPS = 1e-6


def area(c):
    return c.w * c.h


def overlap(a, b):
    ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    return max(ox, 0.0) * max(oy, 0.0)


def by_key(cells):
    return {c.key: c for c in cells}


def children(cells, parent_key):
    return [c for c in cells if c.depth == 2 and c.key.split("/")[0] == parent_key]


def reading_order(cells):
    """Keys in strip order: top strip first, left to right in each strip."""
    return [c.key for c in sorted(cells, key=lambda c: (round(c.y, 6), c.x))]


# Ten top-level directories of different sizes, each with subdirectories, plus root files.
SAMPLE = {
    "README.md": 400,
    "setup.py": 600,
    "alpha/a.py": 900,
    "alpha/core/x.py": 1500,
    "alpha/core/y.py": 700,
    "alpha/io/z.py": 1200,
    "beta/b.py": 3000,
    "docs/guide/intro.md": 800,
    "docs/api.md": 400,
    "gamma/one/a.rs": 2500,
    "gamma/two/b.rs": 2200,
    "gamma/three/c.rs": 1800,
    "lib/util.c": 2000,
    "src/main.c": 1000,
    "src/render/cull.c": 3000,
    "src/render/draw.c": 2400,
    "src/net/sock.c": 1600,
    "tests/t1.py": 1300,
    "tests/fixtures/f.txt": 500,
    "web/app.js": 1700,
    "zeta/z.py": 900,
}


class LayoutGeometryTest(unittest.TestCase):
    def setUp(self):
        self.cells = build_cells(SAMPLE, W, H)
        self.top = [c for c in self.cells if c.depth == 1]
        self.assertEqual(len(self.top), 10)
        self.assertGreater(len(self.cells), 25)

    def test_level1_cells_tile_the_map_with_no_overlap_and_no_gap(self):
        for c in self.top:
            self.assertGreater(c.w, 0)
            self.assertGreater(c.h, 0)
            self.assertGreaterEqual(c.x, -EPS)
            self.assertGreaterEqual(c.y, -EPS)
            self.assertLessEqual(c.x + c.w, W + EPS)
            self.assertLessEqual(c.y + c.h, H + EPS)
        for a, b in itertools.combinations(self.top, 2):
            self.assertLess(overlap(a, b), EPS, (a.key, b.key))
        self.assertAlmostEqual(sum(map(area, self.top)), W * H, places=4)

    def test_level2_cells_tile_their_parent_below_the_label_inset(self):
        inset = 20.0
        cells = build_cells(SAMPLE, W, H, label_inset=inset)
        parents = [c for c in cells if c.depth == 1 and c.key not in (".", "…")]
        self.assertTrue(parents)
        for p in parents:
            kids = children(cells, p.key)
            self.assertTrue(kids, p.key)
            for k in kids:
                self.assertGreaterEqual(k.x, p.x - EPS)
                self.assertGreaterEqual(k.y, p.y + inset - EPS)
                self.assertLessEqual(k.x + k.w, p.x + p.w + EPS)
                self.assertLessEqual(k.y + k.h, p.y + p.h + EPS)
            for a, b in itertools.combinations(kids, 2):
                self.assertLess(overlap(a, b), EPS, (a.key, b.key))
            self.assertAlmostEqual(sum(map(area, kids)), p.w * (p.h - inset), places=4)

    def test_level1_cell_areas_are_proportional_to_weight(self):
        total = sum(SAMPLE.values())
        for c in self.top:
            self.assertAlmostEqual(area(c) / (W * H), c.weight / total, places=9, msg=c.key)

    def test_level2_cell_areas_are_proportional_to_weight_within_their_parent(self):
        for p in self.top:
            kids = children(self.cells, p.key)
            inner = sum(map(area, kids))
            for k in kids:
                self.assertAlmostEqual(area(k) / inner, k.weight / p.weight, places=9, msg=k.key)

    def test_cell_weights_are_the_sums_of_their_files_lines(self):
        cells = by_key(self.cells)
        self.assertEqual(cells["src"].weight, 1000 + 3000 + 2400 + 1600)
        self.assertEqual(cells["src/render"].weight, 3000 + 2400)
        self.assertEqual(cells["src/."].weight, 1000)
        self.assertEqual(cells["."].weight, 1000)

    def test_cells_keep_name_order_in_reading_order(self):
        keys = reading_order(self.top)
        self.assertEqual(keys, sorted(keys))
        for p in self.top:
            kids = reading_order(children(self.cells, p.key))
            self.assertEqual(kids, sorted(kids))

    def test_children_follow_their_parent_in_the_cell_list(self):
        seen_parent = set()
        for c in self.cells:
            if c.depth == 1:
                seen_parent.add(c.key)
            else:
                self.assertIn(c.key.split("/")[0], seen_parent)

    def test_strips_run_horizontally_across_the_whole_map(self):
        rows = {}
        for c in self.top:
            rows.setdefault(round(c.y, 6), []).append(c)
        self.assertGreater(len(rows), 1)
        for row in rows.values():
            self.assertAlmostEqual(sum(c.w for c in row), W, places=6)
            self.assertEqual(len({round(c.h, 6) for c in row}), 1)

    def test_a_strip_ends_when_the_next_item_would_worsen_its_average_aspect_ratio(self):
        # Eight equal items on a 2:1 map. A strip of k items has mean aspect
        # 16/k^2 while k <= 4, so four squares fill it; a fifth item would make
        # it 1.5625, so the fifth item starts the second strip.
        cells = build_cells({f"{k}/x": 1 for k in "abcdefgh"}, 2.0, 1.0)
        top = by_key([c for c in cells if c.depth == 1])
        for k in "abcdefgh":
            self.assertAlmostEqual(top[k].w, 0.5)
            self.assertAlmostEqual(top[k].h, 0.5)
        self.assertAlmostEqual(top["d"].y, 0.0)
        self.assertAlmostEqual(top["e"].y, 0.5)
        self.assertAlmostEqual(top["e"].x, 0.0)

    def test_the_last_strip_joins_the_strip_before_it_when_that_lowers_their_mean_aspect(self):
        # Greedy: a and b fill a strip; c would worsen it, so c starts a strip
        # alone, 2 x 0.02 (aspect 102). Merged, the three items average about 9.
        cells = build_cells({"a/x": 50, "b/x": 50, "c/x": 2}, 2.0, 1.0)
        c = by_key(cells)["c"]
        self.assertAlmostEqual(c.y, 0.0)
        self.assertAlmostEqual(c.h, 1.0)

    def test_the_last_strip_stays_apart_when_joining_would_raise_the_mean_aspect(self):
        cells = build_cells({f"{k}/x": 1 for k in "abcdefgh"}, 2.0, 1.0)
        self.assertAlmostEqual(by_key(cells)["h"].y, 0.5)

    def test_the_last_strip_joins_a_strip_of_several_items(self):
        # Greedy gives [a b] [c]; the joined strip [a b c] has the lower mean.
        cells = build_cells({"a/x": 1, "b/x": 3, "c/x": 3}, 2.0, 1.0)
        self.assertEqual({round(c.y, 9) for c in cells if c.depth == 1}, {0.0})

    def test_a_last_strip_of_several_items_joins_the_strip_before_it(self):
        # Greedy gives [a] [b c]; the joined strip [a b c] has the lower mean.
        cells = build_cells({"a/x": 8, "b/x": 1, "c/x": 1}, 2.0, 1.0)
        self.assertEqual({round(c.y, 9) for c in cells if c.depth == 1}, {0.0})

    def test_only_the_last_two_strips_are_considered_for_joining(self):
        # On a tall map, each of three items is a strip of its own.
        cells = build_cells({"a/x": 2, "b/x": 1, "c/x": 2}, 1.0, 2.0)
        top = [c for c in cells if c.depth == 1]
        self.assertEqual([c.key for c in top], ["a", "b", "c"])
        for c in top:
            self.assertAlmostEqual(c.w, 1.0)
        self.assertAlmostEqual(sum(map(area, top)), 2.0)

    def test_a_tie_keeps_the_greedy_strips(self):
        # Two equal items on a 2 x 2 map: one 2 x 1 strip each, or two 1 x 2
        # cells side by side, both mean aspect 2. The item stays in the strip.
        cells = by_key(build_cells({"a/x": 1, "b/x": 1}, 2.0, 2.0))
        self.assertAlmostEqual(cells["b"].y, 0.0)
        self.assertAlmostEqual(cells["b"].x, 1.0)
        # Weights 2 and 1 on a 1 x 1 map: greedy splits them, and joining
        # them back ties (mean aspect 1.5 either way). The strips stay apart.
        cells = by_key(build_cells({"a/x": 2, "b/x": 1}, 1.0, 1.0))
        self.assertAlmostEqual(cells["b"].x, 0.0)
        self.assertAlmostEqual(cells["b"].w, 1.0)

    def test_a_project_of_one_line_gives_one_cell(self):
        self.assertEqual(build_cells({"a.py": 1}, W, H), [Cell(".", ".", 1, 0.0, 0.0, W, H, 1)])

    def test_the_label_inset_is_at_most_half_a_short_cell(self):
        cells = by_key(build_cells({"a/x": 1}, 100.0, 10.0, label_inset=16.0))
        self.assertAlmostEqual(cells["a/."].y, 5.0)
        self.assertAlmostEqual(cells["a/."].h, 5.0)
        cells = by_key(build_cells({"a/x": 1}, 100.0, 40.0, label_inset=16.0))
        self.assertAlmostEqual(cells["a/."].y, 16.0)

    def test_an_item_that_improves_the_strip_aspect_ratio_stays_in_the_strip(self):
        # On a 4:1 map, four equal items make four squares in one strip.
        cells = build_cells({"a/x": 1, "b/x": 1, "c/x": 1, "d/x": 1}, 4.0, 1.0)
        top = [c for c in cells if c.depth == 1]
        self.assertEqual({round(c.y, 9) for c in top}, {0.0})
        self.assertEqual([round(c.x, 9) for c in top], [0.0, 1.0, 2.0, 3.0])

    def test_cells_have_depth_and_label(self):
        cells = by_key(self.cells)
        self.assertEqual((cells["src"].depth, cells["src"].label), (1, "src"))
        self.assertEqual((cells["src/render"].depth, cells["src/render"].label), (2, "render"))
        self.assertEqual((cells["src/."].depth, cells["src/."].label), (2, "."))
        self.assertEqual((cells["."].depth, cells["."].label), (1, "."))

    def test_cell_is_a_frozen_dataclass_with_the_contract_fields(self):
        names = [f.name for f in dataclasses.fields(Cell)]
        self.assertEqual(names, ["key", "label", "depth", "x", "y", "w", "h", "weight"])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.cells[0].x = 3.0

    def test_no_files_or_no_lines_give_no_cells(self):
        self.assertEqual(build_cells({}, W, H), [])
        self.assertEqual(build_cells({"a.png": 0, "b/c.bin": 0}, W, H), [])


class RootFilesTest(unittest.TestCase):
    def test_files_in_the_root_form_one_cell_with_key_dot(self):
        cells = build_cells({"README.md": 50, "Makefile": 50, "src/a.c": 100}, W, H)
        root = by_key(cells)["."]
        self.assertEqual(root.weight, 100)
        self.assertEqual(root.depth, 1)
        self.assertEqual([c for c in cells if c.key.startswith("./")], [])

    def test_files_directly_in_a_top_level_directory_form_one_level2_dot_cell(self):
        cells = by_key(build_cells({"src/a.c": 30, "src/b.c": 70, "src/x/c.c": 100}, W, H))
        self.assertEqual(cells["src/."].weight, 100)
        self.assertEqual(cells["src/x"].weight, 100)


class SmallCellsTest(unittest.TestCase):
    def test_small_level1_cells_join_one_ellipsis_cell_at_the_end(self):
        weights = {"big/a": 1000, "huge/a": 2000, "aa/a": 10, "zz/a": 12, "README": 5}
        cells = build_cells(weights, W, H)
        top = [c for c in cells if c.depth == 1]
        self.assertEqual([c.key for c in top], ["big", "huge", "…"])
        dots = by_key(cells)["…"]
        self.assertEqual(dots.label, "…")
        self.assertEqual(dots.weight, 27)
        self.assertEqual(children(cells, "…"), [])

    def test_small_level2_cells_join_one_ellipsis_cell_in_their_parent(self):
        weights = {"src/a/x": 500, "src/b/x": 4, "src/c/x": 3, "src/d/x": 493}
        cells = build_cells(weights, W, H)
        kids = children(cells, "src")
        self.assertEqual([c.key for c in kids], ["src/a", "src/d", "src/…"])
        self.assertEqual(kids[-1].label, "…")
        self.assertEqual(kids[-1].weight, 7)
        self.assertEqual(kids[-1].depth, 2)

    def test_the_small_cell_threshold_is_one_and_a_half_percent_of_the_whole_map(self):
        # 15/1000 = 1.5 % stays; 14/1000 joins.
        weights = {"keep/a": 15, "join/a": 14, "big/a": 971}
        top = [c.key for c in build_cells(weights, W, H) if c.depth == 1]
        self.assertEqual(top, ["big", "keep", "…"])
        # The level-2 threshold is measured against the whole map, not the parent.
        weights = {"p/keep/a": 15, "p/join/a": 14, "q/a": 971}
        kids = [c.key for c in children(build_cells(weights, W, H), "p")]
        self.assertEqual(kids, ["p/keep", "p/…"])

    def test_an_ellipsis_cell_of_zero_lines_is_left_out(self):
        weights = {"src/a/x": 100, "src/img/logo.png": 0, "assets/b.png": 0}
        cells = by_key(build_cells(weights, W, H))
        self.assertIn("src/a", cells)
        self.assertNotIn("…", cells)
        self.assertNotIn("src/…", cells)


class LocateTest(unittest.TestCase):
    def setUp(self):
        weights = dict(SAMPLE)
        weights.update({"tiny/t.py": 3, "src/small/s.c": 2, "NOTES": 1})
        self.cells = build_cells(weights, W, H)
        self.keys = by_key(self.cells)

    def test_locate_picks_the_deepest_cell_that_covers_the_path(self):
        self.assertEqual(locate(self.cells, "src/render/cull.c").key, "src/render")
        self.assertEqual(locate(self.cells, "src/render/deep/er/x.c").key, "src/render")

    def test_locate_maps_a_file_directly_in_a_top_level_directory_to_its_dot_cell(self):
        self.assertEqual(locate(self.cells, "src/main.c").key, "src/.")

    def test_locate_maps_a_root_file_to_the_dot_cell(self):
        self.assertEqual(locate(self.cells, "README.md").key, ".")

    def test_locate_maps_paths_of_joined_level2_cells_to_their_ellipsis_cell(self):
        self.assertIn("src/…", self.keys)
        self.assertEqual(locate(self.cells, "src/small/s.c").key, "src/…")

    def test_locate_maps_paths_of_joined_level1_cells_to_the_ellipsis_cell(self):
        self.assertIn("…", self.keys)
        self.assertNotIn("tiny", self.keys)
        self.assertEqual(locate(self.cells, "tiny/t.py").key, "…")

    def test_locate_falls_back_to_the_level1_cell_when_no_level2_cell_covers_the_path(self):
        cells = build_cells({"src/a/x": 100, "src/b.png": 0, "lib/y": 100}, W, H)
        self.assertNotIn("src/.", by_key(cells))
        self.assertEqual(locate(cells, "src/b.png").key, "src")

    def test_locate_returns_none_when_no_cell_covers_the_path(self):
        cells = build_cells({"src/a/x": 100, "lib/y/z": 100}, W, H)
        self.assertEqual(locate(cells, "src/a/x").key, "src/a")
        self.assertIsNone(locate(cells, "docs/readme.md"))
        self.assertIsNone(locate(cells, "README.md"))
        self.assertIsNone(locate([], "src/a/x"))

    def test_locate_does_not_match_a_directory_by_name_prefix(self):
        cells = build_cells({"src/a/x": 100, "srcgen/a/x": 100}, W, H)
        self.assertEqual(locate(cells, "srcgen/a/x").key, "srcgen/a")
        self.assertEqual(locate(cells, "src/ab/x"), by_key(cells)["src"])


class StabilityTest(unittest.TestCase):
    def test_a_small_weight_change_moves_cells_only_a_little(self):
        before = by_key(build_cells(SAMPLE, W, H))
        changed = dict(SAMPLE)
        changed["src/render/cull.c"] += 150  # 0.5 % of the total
        after = by_key(build_cells(changed, W, H))
        self.assertGreater(len(before), 25)
        self.assertEqual(before.keys(), after.keys())
        for key in before:
            b, a = before[key], after[key]
            moved = max(abs(a.x - b.x), abs(a.y - b.y),
                        abs(a.x + a.w - b.x - b.w), abs(a.y + a.h - b.y - b.h))
            self.assertLess(moved, 0.03 * W, key)

    def test_the_same_weights_always_give_the_same_cells(self):
        shuffled = dict(reversed(list(SAMPLE.items())))
        self.assertTrue(build_cells(SAMPLE, W, H))
        self.assertEqual(build_cells(SAMPLE, W, H), build_cells(shuffled, W, H))


def write(root, rel, data):
    p = pathlib.Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class CodeWeightsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_code_weights_counts_the_lines_of_each_file(self):
        write(self.root, "a.py", b"one\ntwo\nthree\n")
        write(self.root, "src/b.py", b"one\ntwo")  # no final newline
        write(self.root, "src/empty.py", b"")
        self.assertEqual(code_weights(self.root),
                         {"a.py": 3, "src/b.py": 2, "src/empty.py": 0})

    def test_binary_files_weigh_zero(self):
        write(self.root, "logo.png", b"\x89PNG\r\n\x00\x00\nmore\nlines\n")
        late_nul = b"line\n" * 2000 + b"\x00\n"  # NUL after the first 8 KiB
        write(self.root, "late.txt", late_nul)
        weights = code_weights(self.root)
        self.assertEqual(weights["logo.png"], 0)
        self.assertEqual(weights["late.txt"], 2001)

    def test_a_nul_byte_at_the_end_of_the_first_8_kib_makes_a_file_binary(self):
        write(self.root, "edge.bin", b"a" * 8191 + b"\x00" + b"\nx\n")
        write(self.root, "past.txt", b"a" * 8192 + b"\x00" + b"\nx\n")
        weights = code_weights(self.root)
        self.assertEqual(weights["edge.bin"], 0)
        self.assertEqual(weights["past.txt"], 2)

    def test_line_counts_span_reads_of_large_files(self):
        write(self.root, "big.txt", b"x" * 100 + (b"y\n" * 300_000))
        self.assertEqual(code_weights(self.root)["big.txt"], 300_000)

    def test_in_a_git_repository_tracked_and_new_unignored_files_count(self):
        # An agent's new file shows on the map before anyone commits it.
        self.git("init", "-q")
        write(self.root, ".gitignore", b"ignored.log\n")
        write(self.root, "tracked.py", b"a\nb\n")
        write(self.root, "dir with space/t.py", b"a\n")
        write(self.root, "untracked.py", b"a\nb\n")
        write(self.root, "ignored.log", b"a\nb\nc\n")
        write(self.root, "node_modules/kept.js", b"a\n")
        self.git("add", "tracked.py", "dir with space/t.py", "node_modules/kept.js")
        self.assertEqual(code_weights(self.root),
                         {".gitignore": 1, "tracked.py": 2, "dir with space/t.py": 1,
                          "untracked.py": 2, "node_modules/kept.js": 1})

    def test_tracked_files_missing_from_the_working_tree_are_skipped(self):
        self.git("init", "-q")
        write(self.root, "kept.py", b"a\n")
        write(self.root, "gone.py", b"a\n")
        self.git("add", "kept.py", "gone.py")
        os.remove(self.root / "gone.py")
        self.assertEqual(code_weights(self.root), {"kept.py": 1})

    def test_outside_git_the_walk_skips_hidden_and_build_directories(self):
        write(self.root, "src/a.py", b"a\n")
        write(self.root, ".hidden_file", b"a\n")
        for skipped in (".git", ".venv", "node_modules", "target", "build",
                        "src/node_modules", "src/.cache", "src/build"):
            write(self.root, f"{skipped}/x.py", b"a\n")
        write(self.root, "src/builder/b.py", b"a\nb\n")
        self.assertEqual(code_weights(self.root),
                         {"src/a.py": 1, ".hidden_file": 1, "src/builder/b.py": 2})

    def test_outside_git_the_walk_uses_forward_slash_relative_paths(self):
        write(self.root, "a/b/c.txt", b"x\n")
        self.assertEqual(list(code_weights(self.root)), ["a/b/c.txt"])


if __name__ == "__main__":
    unittest.main()
