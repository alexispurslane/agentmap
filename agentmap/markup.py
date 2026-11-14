"""HTML from t-strings, with every interpolated value escaped by default.

    html(t'<h3 class="title">{title}</h3>')

The literal text of a t-string is HTML. Each `{value}` is written by one rule:

- `Safe` (HTML that `html()` already made) is written unchanged.
- A list or a tuple is written as its items in order, each by this rule.
- `None` is written as nothing.
- Any other value is converted with `str()` and escaped, quotes included.

A conversion (`!r`) or a format spec (`:.3f`) makes a plain string, so its
result is escaped even when the value was `Safe`.
"""

from html import escape
from string.templatelib import Interpolation, Template, convert


class Safe(str):
    """HTML that is already escaped. Only `html()` makes one."""


def html(template: Template) -> Safe:
    return Safe("".join(
        _write(_formatted(part)) if isinstance(part, Interpolation) else part
        for part in template))


def _formatted(part: Interpolation):
    value = part.value
    if part.conversion:
        value = convert(value, part.conversion)
    if part.format_spec:
        value = format(value, part.format_spec)
    return value


def _write(value) -> str:
    if isinstance(value, Safe):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(_write(v) for v in value)
    if value is None:
        return ""
    return escape(str(value))
