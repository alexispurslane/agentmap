from agentmap.components._template import render


def hover(tag: str, enter: str) -> str:
    """Datastar attributes that link every mark of one thread through `$hover`.

    The element sets `$hover` to `tag` on the event `enter` and clears it on
    mouseleave. It has the class `hl` while `$hover` holds `tag`. Other
    templates include it as `{{> hover}}`. Tags hold only A-Z, 0-9 and `.`.
    """
    return render("hover", {"tag": tag, "enter": enter})
