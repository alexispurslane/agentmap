from urllib.parse import quote

from agentmap.components._template import render
from agentmap.components.view import element_id


def link(tags: list[str], hue: float | None, peek: tuple[str, str] | None = None,
         place: str = "") -> str:
    """Datastar attributes of a mark that belongs to several threads and may have a box.

    Such marks are an epic column, a ticket in it, a ticket row, a ticket badge
    and a map cell. The mark belongs to the threads with the tags `tags`. A
    hover sets the focus `$focus` to all of them and `$hue` to `hue`, the hue of
    the first, so the mark and every mark of those threads light. The mark has
    the class `hl` while one of its threads is in `$focus`.

    `peek` is the subject of the mark's box: (kind, id), where the kind is `t`
    for a ticket and `c` for a map cell. A hover opens the box: it sets `$peek`
    to the subject's key, `element_id(kind, id)`, and loads `/peek/<kind>/<id>`
    once, unless a box is pinned. A click pins the box to this mark: it sets
    `$pin` to the key, `$pinat` to the mark's id `<place>-<key>`, and loads
    `/pin/<kind>/<id>`. A click on the pinned mark unpins it. The mark id is
    unique on the page, because one ticket can show in several places, and the
    pinned mark is the box's anchor. The handlers stop at the innermost mark,
    so a chip or a ticket inside a column wins over the column. Other templates
    include it as `{{> link}}` with `link_data`.
    """
    return render("link", link_data(tags, hue, peek, place))


def link_data(tags: list[str], hue: float | None, peek: tuple[str, str] | None = None,
              place: str = "") -> dict:
    """The template data of `{{> link}}`."""
    key = element_id(*peek) if peek is not None else ""
    return {
        "link_focus": "".join(f" {t} " for t in tags).replace("  ", " "),
        "link_hue": "" if hue is None or not tags else hue,
        "link_lit": " || ".join(f"$focus.includes(' {t} ')" for t in tags) or "false",
        "link_peek": peek is not None,
        "peek_dom": key,
        "peek_url": f"{peek[0]}/{quote(peek[1], safe='')}" if peek is not None else "",
        "peek_mark": f"{place}-{key}" if peek is not None else "",
    }
