from datetime import datetime

from agentmap.components._template import render
from agentmap.components.view import Forest, age_class, clock, element_id
from agentmap.events import Event

# At most this many entries, after edits are grouped.
LIMIT = 60


def event_log(events: list[Event], forest: Forest, now: datetime) -> str:
    """The events module: what happened, newest first, inked by age.

    Events of threads outside the snapshot are left out. A run of edits by one
    thread, with no other event between them, shows as one entry: the newest
    file and how many more.
    """
    entries: list[dict] = []
    for e in sorted(events, key=lambda e: e.at, reverse=True):
        if e.thread_id not in forest.tag:
            continue
        last = entries[-1] if entries else None
        if (e.kind == "edits" and last is not None and last["kind"] == "edits"
                and last["thread"] == e.thread_id):
            if e.text != last["path"] and e.text not in last["paths"]:
                last["paths"].add(e.text)
                last["more"] = len(last["paths"])
            continue
        if len(entries) == LIMIT:
            break
        entries.append({
            "id": element_id("e", f"{e.thread_id}|{e.kind}|{e.at.isoformat()}|{e.text}"),
            "thread": e.thread_id, "kind": e.kind, "verb": e.kind,
            "tag": forest.tag[e.thread_id], "hue": forest.hue[e.thread_id],
            "enter": "mouseenter", "clock": clock(e.at), "age": age_class(now, e.at),
            "path": e.text if e.kind == "edits" else "",
            "text": "" if e.kind == "edits" else e.text,
            "paths": set(), "more": 0,
        })
    return render("event_log", {"events": entries})
