from urllib.parse import quote

from agentmap.components._template import render
from agentmap.components.view import element_id


def link(tags: list[str], hue: float | None, ticket_id: str | None = None, place: str = "") -> str:
    """Datastar attributes of a ticket mark: an epic column, a ticket in it, a ticket row.

    A ticket mark belongs to the threads that work on its ticket or group, with
    the tags `tags` in tag order. A hover sets the focus `$focus` to all of them
    and `$hue` to `hue`, the hue of the first, so the mark and every mark of
    those threads light. The mark has the class `hl` while one of its threads
    is in `$focus`. With `ticket_id`, a hover also opens the ticket's hover box:
    it sets `$peek` and loads `/peek/<id>` once, unless a box is pinned. A
    click pins the box to this mark: it sets `$pin` to the ticket, `$pinat` to
    the mark's id `<place>-<ticket element id>`, and loads `/pin/<id>`. A click
    on the pinned mark unpins it. The mark id is unique on the page, because one
    ticket can show in several places, and the pinned mark is the box's anchor.
    The handlers stop at the innermost mark, so a chip or a ticket inside a
    column wins over the column. Other templates include it as `{{> link}}` with
    `link_data`.
    """
    return render("link", link_data(tags, hue, ticket_id, place))


def link_data(tags: list[str], hue: float | None, ticket_id: str | None = None,
              place: str = "") -> dict:
    """The template data of `{{> link}}`."""
    return {
        "link_focus": "".join(f" {t} " for t in tags).replace("  ", " "),
        "link_hue": "" if hue is None or not tags else hue,
        "link_lit": " || ".join(f"$focus.includes(' {t} ')" for t in tags) or "false",
        "link_peek": ticket_id is not None,
        "peek_dom": element_id("t", ticket_id) if ticket_id is not None else "",
        "peek_url": quote(ticket_id, safe="") if ticket_id is not None else "",
        "peek_mark": f"{place}-{element_id('t', ticket_id)}" if ticket_id is not None else "",
    }
