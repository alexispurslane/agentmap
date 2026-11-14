from agentmap.components._template import render


def page(app_html: str) -> str:
    """The whole document around the app. It opens the event stream when it loads.

    `app_html` is the HTML of `<main id="app">`, written unchanged. The signal
    `$hover` lives on `<body>`, outside the morphed app, so an update keeps it.
    """
    return render("page", {"app": app_html})
