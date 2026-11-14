import unittest

from agentmap.markup import Safe, html


class EscapingTest(unittest.TestCase):
    def test_an_interpolated_string_is_escaped(self):
        title = "<script>alert(1)</script>"
        self.assertEqual(html(t"<h3>{title}</h3>"),
                         "<h3>&lt;script&gt;alert(1)&lt;/script&gt;</h3>")

    def test_quotes_are_escaped_so_a_value_cannot_leave_its_attribute(self):
        key = 'a" onclick="x'
        self.assertEqual(html(t'<div data-cell="{key}">'),
                         '<div data-cell="a&quot; onclick=&quot;x">')

    def test_the_literal_text_of_the_template_is_kept_as_written(self):
        self.assertEqual(html(t'<p class="a">x & y</p>'), '<p class="a">x & y</p>')

    def test_a_number_is_written_as_text(self):
        n = 3
        self.assertEqual(html(t"<b>{n}</b>"), "<b>3</b>")


class CompositionTest(unittest.TestCase):
    def test_the_result_is_marked_safe(self):
        self.assertIsInstance(html(t"<p></p>"), Safe)

    def test_safe_html_is_inserted_without_a_second_escape(self):
        inner = html(t"<i>{'a<b'}</i>")
        self.assertEqual(html(t"<p>{inner}</p>"), "<p><i>a&lt;b</i></p>")

    def test_a_list_is_written_as_its_items_in_order_each_by_the_same_rule(self):
        items = [html(t"<li>a</li>"), "<li>", html(t"<li>c</li>")]
        self.assertEqual(html(t"<ol>{items}</ol>"), "<ol><li>a</li>&lt;li&gt;<li>c</li></ol>")

    def test_none_is_written_as_nothing(self):
        missing = None
        self.assertEqual(html(t"<p>{missing}</p>"), "<p></p>")


class FormatTest(unittest.TestCase):
    def test_a_format_spec_is_applied_before_the_value_is_written(self):
        x = 1.5
        self.assertEqual(html(t'<div style="left:{x:.3f}%">'), '<div style="left:1.500%">')

    def test_a_conversion_is_applied_and_its_result_is_escaped(self):
        s = "<"
        self.assertEqual(html(t"{s!r}"), "&#x27;&lt;&#x27;")

    def test_a_format_spec_on_safe_html_escapes_the_result(self):
        inner = Safe("<b>")
        self.assertEqual(html(t"{inner:>4}"), " &lt;b&gt;")


if __name__ == "__main__":
    unittest.main()
