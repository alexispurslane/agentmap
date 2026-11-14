from datetime import datetime

from agentmap.components._template import render
from agentmap.components.plan_card.plan_card import plan_card
from agentmap.components.view import Forest
from agentmap.plan import Plan


def plan_board(forest: Forest, plans: dict[str, Plan], now: datetime) -> str:
    """The plans module: the plan of every thread in the snapshot, in tag order.

    A plan with neither a goal nor items has nothing to show and is left out.
    """
    cards = [plan_card(t.id, forest.tag[t.id], forest.hue[t.id], plans[t.id], now)
             for t in forest.walk()
             if t.id in plans and (plans[t.id].goal or plans[t.id].items)]
    return render("plan_board", {
        "cards": "".join(cards),
        "count": f"{len(cards)} plan" + ("" if len(cards) == 1 else "s"),
    })
