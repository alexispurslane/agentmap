from datetime import datetime

from agentmap.components._template import render
from agentmap.components.view import age_class, element_id
from agentmap.plan import Plan


def plan_card(thread_id: str, tag: str, hue: float, plan: Plan, now: datetime) -> str:
    """One thread's plan: its goal and its two-level todo list, inked by age.

    A top-level item with children shows a count of done children. A done
    top-level item hides its children, so it takes one line.
    """
    def item(i) -> dict:
        return {"id": element_id("i", f"{thread_id}#{i.id}"), "state": i.state,
                "text": i.text, "age": age_class(now, i.changed_at)}

    items = []
    for top in plan.items:
        entry = item(top)
        done = sum(k.state == "done" for k in top.children)
        entry["count"] = f"{done}/{len(top.children)}" if top.children else ""
        entry["children"] = [] if top.state == "done" else [item(k) for k in top.children]
        entry["has_children"] = bool(entry["children"])
        items.append(entry)
    return render("plan_card", {
        "id": element_id("p", thread_id), "tag": tag, "hue": hue, "enter": "mouseenter",
        "goal": plan.goal, "goal_age": age_class(now, plan.updated_at), "items": items,
    })
