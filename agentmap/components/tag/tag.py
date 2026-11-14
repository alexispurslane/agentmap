from agentmap.components._template import render


def tag(tag: str, hue: float) -> str:
    """A thread's tag, such as `A1.2`, on its own hue. Other templates include it as `{{> tag}}`."""
    return render("tag", {"tag": tag, "hue": hue})
