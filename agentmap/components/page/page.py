from agentmap.components._template import render


def page(app_html: str, epics_html: str, tickets_html: str) -> str:
    """The whole document. It opens the event stream when it loads.

    The body holds three fragments that the server patches apart:
    `<main id="app">`, `<div id="tickets">` and `<section id="epics">`. The
    signals `$focus`, `$hue`, `$epic`, `$peek`, `$pin`, `$pinat`, `$stale` and `$now` live on `<body>`, outside
    every fragment, so an update keeps them. `$now` is the Unix time, which the
    page sets every 10 seconds so that marks fade in real time.
    """
    return render("page", {"app": app_html, "epics": epics_html, "tickets": tickets_html})
