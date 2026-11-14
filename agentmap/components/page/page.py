from agentmap.components._template import render


def is_dark(hour: int, hours: tuple[int, int]) -> bool:
    """True if the board is dark at the local `hour` (0 to 23).

    `hours` is (light_from, dark_from): the board is light from the hour
    light_from up to the hour dark_from, and dark the rest of the day.
    """
    light_from, dark_from = hours
    return hour < light_from or hour >= dark_from


def page(app_html: str, epics_html: str, tickets_html: str, hours: tuple[int, int] = (7, 19),
         dark: bool = False) -> str:
    """The whole document. It opens the event stream when it loads.

    The body holds three fragments that the server patches apart:
    `<main id="app">`, `<div id="tickets">` and `<section id="epics">`. The
    signals `$focus`, `$hue`, `$epic`, `$peek`, `$pin`, `$pinat`, `$stale` and `$now` live on `<body>`, outside
    every fragment, so an update keeps them. `$now` is the Unix time, which the
    page sets every 10 seconds so that marks fade in real time.

    The body has the class `dark` outside the light hours `hours` of the
    browser's local clock (see `is_dark`), which the CSS turns into the dark
    theme. `dark` is the server's answer for the first paint, so that the page
    does not show the light theme for a moment at night.
    """
    return render("page", {"app": app_html, "epics": epics_html, "tickets": tickets_html,
                           "light_from": hours[0], "dark_from": hours[1], "dark": dark})
