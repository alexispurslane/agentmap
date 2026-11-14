from datetime import datetime

from agentmap.components._template import render

# A mark is fully faded this many seconds after its event.
FADE_SECONDS = 3600


def fade(at: datetime | None) -> str:
    """The Datastar attribute that fades a mark in real time from its event's time `at`.

    It sets `--fade` = min(1, sqrt(age / FADE_SECONDS)), from the `$now` signal
    that the page ticks. The square root makes the first minutes change the
    most. A mark without a time is fully faded. Other templates include it as
    `{{> fade}}` with the Unix time in `fade_at`.
    """
    return render("fade", fade_data(at))


def fade_data(at: datetime | None) -> dict:
    """The template data of `{{> fade}}`."""
    return {"fade_at": int(at.timestamp()) if at else 0, "fade_seconds": FADE_SECONDS}
