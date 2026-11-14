from datetime import datetime, timedelta

from agentmap.components._template import render
from agentmap.components.fade.fade import fade_data
from agentmap.components.view import STATE_CLASS, element_id, format_age, summary
from agentmap.status import Status
from agentmap.t3 import AgentThread

# An idle child thread that is quiet for longer than this shows as one line.
COMPACT_AFTER = timedelta(minutes=10)


def agent_row(thread: AgentThread, tag: str, hue: float, status: Status | None,
              kids_html: str, is_root: bool, now: datetime, *, badges: list[str] = (),
              title: str | None = None) -> str:
    """One thread's row in the agent tree, with its children's rows inside it.

    A full row shows the current tool call (while the thread works) and one
    summary line: the newest of the agent's posted status, its last message
    and its newest thought. An idle child quiet for over 10 minutes is one line.
    `badges` are the HTML of its ticket badges, which sit in a fixed slot before
    the state. `title` replaces the thread's title, for example without its ids.
    """
    age = format_age(now - thread.updated_at)
    line = summary(thread, status)
    meta = " · ".join(x for x in (thread.provider, thread.model) if x)
    return render("agent_row", {
        "id": element_id("t", thread.id),
        "tag": tag,
        "hue": hue,
        "enter": "mouseover__stop",
        "title": thread.title if title is None else title,
        "badges": list(badges),
        "state": STATE_CLASS[thread.state],
        "state_label": {"working": "working", "needs_you": "needs you",
                        "idle": f"idle {age}", "failed": f"failed {age}"}[thread.state],
        **fade_data(thread.updated_at),
        "compact": (not is_root and thread.state == "idle"
                    and now - thread.updated_at > COMPACT_AFTER),
        "tool": thread.now,
        "summary": line and {"kind": line.kind, "text": line.text, **fade_data(line.at)},
        "meta": f"{meta} · started {format_age(now - thread.created_at)} ago",
        "kids": kids_html,
    })
