from collections.abc import Mapping
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_chip.agent_chip import agent_chip
from agentmap.components.link.link import link_data
from agentmap.components.view import Forest, age_class, format_age, group_key, group_threads, element_id, ticket_state
from agentmap.tickets import Group, Ticket

# A column lists at most this many of its tickets in progress.
NOW_MAX = 5


def epic_column(group: Group, tickets: Mapping[str, Ticket], links: Mapping[str, list[str]],
                forest: Forest, now: datetime) -> str:
    """One group in the epic strip: its title, counts, progress bar, agents and newest change.

    A click opens the group's ticket list in place of the event log. The column
    and each ticket in it are ticket marks: a hover focuses their threads. A
    hover on a ticket also opens its hover box, and a click pins the box.
    """
    states = [ticket_state(t, tickets) for t in group.members]
    workers = group_threads(group, links, forest)
    total = len(group.members) + group.closed
    active = states.count("in_progress")
    newest = max([t.changed_at for t in group.members]
                 + ([group.root.changed_at] if group.root else []))
    root = group.root
    working = [t for t in group.members if t.status == "in_progress"]
    return render("epic_column", {
        "now": [{"id": t.id, "title": t.title, **_link(links.get(t.id, []), forest, t.id, "c"),
                 "agents": [agent_chip(forest.tag[a], forest.hue[a]) for a in links.get(t.id, [])]}
                for t in working[:NOW_MAX]],
        "more": len(working) - NOW_MAX if len(working) > NOW_MAX else 0,
        "key": element_id("g", group_key(group)),
        "id": root.id if root else "",
        "title": root.title if root else "No epic",
        "priority": root.priority if root else None,
        "has_priority": root is not None,
        "tags": ", ".join(root.tags) if root and root.tags else "",
        "in_progress": active, "ready": states.count("ready"), "blocked": states.count("blocked"),
        "closed": group.closed,
        "done_pct": round(100 * group.closed / total, 1),
        "active_pct": round(100 * active / total, 1),
        "agents": [agent_chip(forest.tag[t], forest.hue[t]) for t in workers],
        **_link(workers, forest),
        "age": age_class(now, newest),
        "changed": _ago(now - newest),
    })


def _link(workers: list[str], forest: Forest, ticket_id: str | None = None, place: str = "") -> dict:
    """The `{{> link}}` data of a mark that these threads work on."""
    return link_data([forest.tag[t] for t in workers], forest.hue[workers[0]] if workers else None,
                     ticket_id, place)


def _ago(delta) -> str:
    """`now` for under a minute, else for example `12m ago`."""
    age = format_age(delta)
    return age if age == "now" else f"{age} ago"
