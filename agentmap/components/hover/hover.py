from agentmap.components._template import render


def hover(tag: str, hue: float, enter: str) -> str:
    """Datastar attributes of a thread mark: a dot, a route, a row, an event or a chip.

    On the event `enter`, the mark sets the focus `$focus` to its one thread and
    `$hue` to the thread's hue. On mouseleave, it clears `$focus`. It has the
    class `hl` while its thread is in `$focus`, whatever mark set the focus.
    `$focus` holds tags each with a space before and after, so " A2 " does not
    match " A2.3 ". Tags hold only A-Z, 0-9 and `.`. Other templates include it
    as `{{> hover}}`. Ticket marks use `{{> link}}` instead.
    """
    return render("hover", {"tag": tag, "hue": hue, "enter": enter})
