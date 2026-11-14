from agentmap.components._template import render


def close() -> str:
    """The button that closes the open ticket list. Other templates include it as `{{> close}}`."""
    return render("close", {})
