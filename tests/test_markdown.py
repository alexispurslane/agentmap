"""Tests of agentmap.markdown, the small Markdown subset of the ticket text."""

import unittest

from agentmap.markdown import fenced_blocks, render


class Paragraphs(unittest.TestCase):
    def test_plain_text_becomes_a_paragraph(self):
        self.assertEqual(render("Hello."), "<p>Hello.</p>")

    def test_the_lines_of_a_paragraph_stay_in_one_paragraph(self):
        self.assertEqual(render("one\ntwo"), "<p>one\ntwo</p>")

    def test_a_blank_line_starts_a_new_paragraph(self):
        self.assertEqual(render("one\n\n  \ntwo"), "<p>one</p>\n<p>two</p>")

    def test_empty_text_gives_no_html(self):
        self.assertEqual(render(""), "")
        self.assertEqual(render("\n\n"), "")

    def test_the_lines_of_a_paragraph_lose_their_outer_white_space(self):
        self.assertEqual(render("  one  \n  two"), "<p>one\ntwo</p>")


class Escaping(unittest.TestCase):
    def test_html_in_the_text_is_escaped(self):
        self.assertEqual(render("<script>alert('x') & \"y\"</script>"),
                         "<p>&lt;script&gt;alert(&#x27;x&#x27;) &amp; &quot;y&quot;"
                         "&lt;/script&gt;</p>")

    def test_html_in_a_heading_an_item_and_a_code_span_is_escaped(self):
        self.assertEqual(render("# <b>\n\n- <i>\n\n`<u>`"),
                         "<h4>&lt;b&gt;</h4>\n<ul><li>&lt;i&gt;</li></ul>\n"
                         "<p><code>&lt;u&gt;</code></p>")

    def test_html_in_bold_and_emphasis_is_escaped(self):
        self.assertEqual(render("**<b>** *<i>* _<u>_"),
                         "<p><strong>&lt;b&gt;</strong> <em>&lt;i&gt;</em> <em>&lt;u&gt;</em></p>")


class Headings(unittest.TestCase):
    def test_headings_of_one_to_six_hashes_are_drawn_as_h4_to_h6(self):
        text = "\n".join("#" * n + f" H{n}" for n in range(1, 7))
        self.assertEqual(render(text), "\n".join([
            "<h4>H1</h4>", "<h5>H2</h5>", "<h6>H3</h6>",
            "<h6>H4</h6>", "<h6>H5</h6>", "<h6>H6</h6>"]))

    def test_a_hash_without_a_space_is_not_a_heading(self):
        self.assertEqual(render("#tag"), "<p>#tag</p>")

    def test_seven_hashes_are_not_a_heading(self):
        self.assertEqual(render("####### x"), "<p>####### x</p>")

    def test_a_heading_line_ends_the_paragraph_before_it(self):
        self.assertEqual(render("text\n## Part"), "<p>text</p>\n<h5>Part</h5>")

    def test_a_heading_carries_inline_formatting(self):
        self.assertEqual(render("# A `b` **c**"),
                         "<h4>A <code>b</code> <strong>c</strong></h4>")


class Lists(unittest.TestCase):
    def test_dash_and_star_items_form_one_unordered_list(self):
        self.assertEqual(render("- a\n* b"), "<ul><li>a</li><li>b</li></ul>")

    def test_numbered_items_form_an_ordered_list(self):
        self.assertEqual(render("1. a\n2. b\n10. c"), "<ol><li>a</li><li>b</li><li>c</li></ol>")

    def test_a_line_that_is_not_an_item_continues_the_item_before_it(self):
        self.assertEqual(render("- a\n  more\n- b"), "<ul><li>a\nmore</li><li>b</li></ul>")

    def test_a_blank_line_ends_a_list(self):
        self.assertEqual(render("- a\n\nafter"), "<ul><li>a</li></ul>\n<p>after</p>")

    def test_an_item_ends_the_paragraph_before_it(self):
        self.assertEqual(render("text\n- a"), "<p>text</p>\n<ul><li>a</li></ul>")

    def test_a_change_of_marker_kind_starts_a_new_list(self):
        self.assertEqual(render("- a\n1. b"), "<ul><li>a</li></ul>\n<ol><li>b</li></ol>")

    def test_an_indented_item_joins_the_same_list(self):
        self.assertEqual(render("- a\n  - b"), "<ul><li>a</li><li>b</li></ul>")

    def test_a_marker_without_a_space_is_not_an_item(self):
        self.assertEqual(render("-a\n*b*\n1.5"), "<p>-a\n<em>b</em>\n1.5</p>")

    def test_an_item_carries_inline_formatting(self):
        self.assertEqual(render("- **a** `b`"),
                         "<ul><li><strong>a</strong> <code>b</code></li></ul>")


class FencedCode(unittest.TestCase):
    def test_a_fenced_block_keeps_its_lines_literally(self):
        self.assertEqual(render("```\n**a** <b>\n  - c\n```"),
                         "<pre><code>**a** &lt;b&gt;\n  - c</code></pre>")

    def test_the_info_string_of_a_fence_is_not_shown(self):
        self.assertEqual(render("```python\nx = 1\n```"), "<pre><code>x = 1</code></pre>")

    def test_a_tilde_fence_works_as_a_backtick_fence(self):
        self.assertEqual(render("~~~\nx\n~~~"), "<pre><code>x</code></pre>")

    def test_a_fence_that_never_closes_runs_to_the_end(self):
        self.assertEqual(render("```\nx\n\ny"), "<pre><code>x\n\ny</code></pre>")

    def test_a_shorter_or_other_fence_inside_a_long_fence_is_text(self):
        self.assertEqual(render("````\n```\n~~~~\n````\nafter"),
                         "<pre><code>```\n~~~~</code></pre>\n<p>after</p>")

    def test_a_fence_with_text_after_it_does_not_close_the_block(self):
        self.assertEqual(render("```\n``` x\n```"), "<pre><code>``` x</code></pre>")

    def test_a_fence_ends_the_paragraph_and_the_list_before_it(self):
        self.assertEqual(render("p\n```\nx\n```\n- a\n```\ny\n```"),
                         "<p>p</p>\n<pre><code>x</code></pre>\n<ul><li>a</li></ul>\n"
                         "<pre><code>y</code></pre>")

    def test_an_empty_fenced_block_gives_an_empty_code_block(self):
        self.assertEqual(render("```\n```"), "<pre><code></code></pre>")

    def test_fenced_blocks_gives_the_open_and_close_line_of_each_block(self):
        lines = ["a", "```", "x", "```", "~~~~", "~~~", "~~~~", "```", "y"]
        self.assertEqual(fenced_blocks(lines), [(1, 3), (4, 6), (7, None)])

    def test_a_fence_indented_four_spaces_is_not_a_fence(self):
        self.assertEqual(fenced_blocks(["    ```", "x"]), [])
        self.assertEqual(fenced_blocks(["   ```", "x", "   ```"]), [(0, 2)])

    def test_two_backticks_are_not_a_fence(self):
        self.assertEqual(fenced_blocks(["``", "x", "``"]), [])

    def test_a_backtick_fence_whose_info_string_holds_a_backtick_is_not_a_fence(self):
        self.assertEqual(fenced_blocks(["``` a`b", "x"]), [])

    def test_a_tilde_fence_whose_info_string_holds_a_backtick_is_a_fence(self):
        self.assertEqual(fenced_blocks(["~~~ a`b", "x", "~~~"]), [(0, 2)])


class Inline(unittest.TestCase):
    def test_a_code_span_keeps_its_text_literally(self):
        self.assertEqual(render("a `**b** _c_` d"), "<p>a <code>**b** _c_</code> d</p>")

    def test_double_stars_make_bold(self):
        self.assertEqual(render("a **b c** d"), "<p>a <strong>b c</strong> d</p>")

    def test_single_stars_and_underscores_make_emphasis(self):
        self.assertEqual(render("*a* _b_"), "<p><em>a</em> <em>b</em></p>")

    def test_emphasis_inside_bold_is_kept(self):
        self.assertEqual(render("**a *b* c**"), "<p><strong>a <em>b</em> c</strong></p>")

    def test_underscores_inside_a_word_are_not_emphasis(self):
        self.assertEqual(render("snake_case_name"), "<p>snake_case_name</p>")

    def test_a_star_between_spaces_is_not_emphasis(self):
        self.assertEqual(render("2 * 3 * 4"), "<p>2 * 3 * 4</p>")

    def test_a_star_before_a_space_does_not_close_emphasis(self):
        self.assertEqual(render("*a *"), "<p>*a *</p>")

    def test_double_stars_around_spaces_are_not_bold(self):
        self.assertEqual(render("** a **"), "<p>** a **</p>")

    def test_an_underscore_between_spaces_is_not_emphasis(self):
        self.assertEqual(render("_ a _"), "<p>_ a _</p>")

    def test_bold_can_span_two_lines_of_a_paragraph(self):
        self.assertEqual(render("**a\nb**"), "<p><strong>a\nb</strong></p>")

    def test_an_http_or_https_link_keeps_its_href(self):
        self.assertEqual(
            render("[a](http://x.org/p) [b](HTTPS://y.org)"),
            '<p><a href="http://x.org/p" target="_blank" rel="noopener noreferrer">a</a> '
            '<a href="HTTPS://y.org" target="_blank" rel="noopener noreferrer">b</a></p>')

    def test_a_link_with_another_scheme_keeps_only_its_text(self):
        self.assertEqual(render("[a](javascript:alert(1)) [b](docs/x.md) [c](httpx://z)"),
                         "<p>a) b c</p>")

    def test_quotes_and_ampersands_in_a_link_target_are_escaped(self):
        self.assertEqual(
            render('[a](https://x.org/?q="b"&c=\'d\')'),
            '<p><a href="https://x.org/?q=&quot;b&quot;&amp;c=&#x27;d&#x27;" target="_blank"'
            ' rel="noopener noreferrer">a</a></p>')

    def test_the_text_of_a_link_carries_inline_formatting(self):
        self.assertEqual(render("[**a** `b`](https://x.org)"),
                         '<p><a href="https://x.org" target="_blank" rel="noopener noreferrer">'
                         "<strong>a</strong> <code>b</code></a></p>")

    def test_underscores_and_stars_in_a_link_target_are_not_emphasis(self):
        self.assertEqual(render("[a](https://x.org/a_b_c*d*)"),
                         '<p><a href="https://x.org/a_b_c*d*" target="_blank"'
                         ' rel="noopener noreferrer">a</a></p>')

    def test_a_bracket_without_a_target_is_text(self):
        self.assertEqual(render("[a] (b)"), "<p>[a] (b)</p>")


if __name__ == "__main__":
    unittest.main()
