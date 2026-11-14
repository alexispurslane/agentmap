from agentmap.components._template import render


def agent_chip(tag: str, hue: float) -> str:
    """A thread's tag that links to its dot and row through `$hover`."""
    return render("agent_chip", {"tag": tag, "hue": hue, "enter": "mouseenter"})
