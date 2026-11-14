from agentmap.components._template import render


def agent_chip(tag: str, hue: float) -> str:
    """A thread's tag, a thread mark. It sits inside ticket marks, so its hover stops there."""
    return render("agent_chip", {"tag": tag, "hue": hue, "enter": "mouseover__stop"})
