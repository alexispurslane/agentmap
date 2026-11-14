"""A small Markdown subset for the ticket text.

`render` turns ticket Markdown into HTML that is safe to insert in the page.
Every piece of the input passes through `html.escape` before it reaches the
output, so the output holds no raw HTML from the input.

The subset:

- Blocks: fenced code blocks (``` or ~~~), headings (`#` to `######`, drawn
  as h4 to h6), unordered lists (`-` or `*`), ordered lists (`1.`) and
  paragraphs. A blank line ends a paragraph or a list. A line that starts a
  block ends the paragraph or the list before it. Any other line continues the
  paragraph or the last list item. Nested lists are drawn flat.
- Inline: code spans, `**bold**`, `*emphasis*`, `_emphasis_` and links. A link
  keeps its href only for http and https URLs, and opens in a new tab.
  Otherwise only its text shows.
"""

from __future__ import annotations

import re
from html import escape

# An opening fence: up to 3 spaces, then 3 or more backticks or tildes, then the info string.
_OPEN_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_CLOSE_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")
_HEADING = re.compile(r"(#{1,6})[ \t]+(.*)")
_ITEM = re.compile(r"[ \t]*([-*]|\d+\.)[ \t]+(.*)")
_INLINE = re.compile(
    r"`([^`]+)`"                                  # 1: a code span
    r"|\[([^\]]+)\]\(([^)\s]+)\)"                 # 2, 3: a link's text and target
    r"|\*\*(?=\S)(.+?)(?<=\S)\*\*"                # 4: bold
    r"|\*(?=[^\s*])(.+?)(?<=[^\s*])\*"            # 5: emphasis with stars
    r"|(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)",        # 6: emphasis with underscores
    re.DOTALL)


def fenced_blocks(lines: list[str]) -> list[tuple[int, int | None]]:
    """The fenced code blocks of these lines, as (opening line, closing line) indexes.

    The closing line is None for a block that runs to the end. A fence closes
    only with the same character, at least as many times, and nothing after it.
    """
    blocks: list[tuple[int, int | None]] = []
    i = 0
    while i < len(lines):
        opening = _OPEN_FENCE.fullmatch(lines[i])
        if opening is None or ("`" in opening[1] and "`" in opening[2]):
            i += 1
            continue
        fence = opening[1]
        close = next((j for j in range(i + 1, len(lines))
                      if (m := _CLOSE_FENCE.fullmatch(lines[j]))
                      and set(m[1]) == set(fence) and len(m[1]) >= len(fence)), None)
        blocks.append((i, close))
        i = len(lines) if close is None else close + 1
    return blocks


def render(text: str) -> str:
    """The HTML of a Markdown text in the subset of this module, safe to insert."""
    lines = text.splitlines()
    fences = dict(fenced_blocks(lines))
    html: list[str] = []
    paragraph: list[str] = []
    items: list[list[str]] = []
    list_tag = ""

    def end_paragraph() -> None:
        if paragraph:
            html.append(f"<p>{_inline(chr(10).join(paragraph))}</p>")
            paragraph.clear()

    def end_list() -> None:
        if items:
            body = "".join(f"<li>{_inline(chr(10).join(item))}</li>" for item in items)
            html.append(f"<{list_tag}>{body}</{list_tag}>")
            items.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if i in fences:
            end_paragraph()
            end_list()
            close = fences[i]
            end = len(lines) if close is None else close
            html.append(f"<pre><code>{escape(chr(10).join(lines[i + 1:end]))}</code></pre>")
            i = end + 1
            continue
        i += 1
        heading = _HEADING.fullmatch(line)
        item = _ITEM.fullmatch(line)
        if not line.strip() or heading:
            end_paragraph()
            end_list()
            if heading:
                tag = f"h{min(len(heading[1]) + 3, 6)}"
                html.append(f"<{tag}>{_inline(heading[2].strip())}</{tag}>")
        elif item:
            end_paragraph()
            tag = "ul" if item[1] in ("-", "*") else "ol"
            if tag != list_tag:
                end_list()
                list_tag = tag
            items.append([item[2].strip()])
        elif items:
            items[-1].append(line.strip())
        else:
            paragraph.append(line.strip())
    end_paragraph()
    end_list()
    return "\n".join(html)


def _inline(text: str) -> str:
    """The HTML of one block's text: code spans, links, bold and emphasis."""
    out: list[str] = []
    at = 0
    for m in _INLINE.finditer(text):
        out.append(escape(text[at:m.start()]))
        at = m.end()
        if m[1] is not None:
            out.append(f"<code>{escape(m[1])}</code>")
        elif m[2] is not None:
            label = _inline(m[2])
            if m[3].lower().startswith(("http://", "https://")):
                out.append(f'<a href="{escape(m[3])}" target="_blank"'
                           f' rel="noopener noreferrer">{label}</a>')
            else:
                out.append(label)
        elif m[4] is not None:
            out.append(f"<strong>{_inline(m[4])}</strong>")
        else:
            out.append(f"<em>{_inline(m[5] if m[5] is not None else m[6])}</em>")
    out.append(escape(text[at:]))
    return "".join(out)
