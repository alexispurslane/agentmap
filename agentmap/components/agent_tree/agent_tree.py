from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_row.agent_row import agent_row
from agentmap.components.view import Forest
from agentmap.plan import Plan
from agentmap.t3 import AgentThread


def agent_tree(forest: Forest, plans: dict[str, Plan], now: datetime) -> str:
    """The agents module: one row per thread, each child inside its parent's row.

    Root threads with work in their tree (working, or needing the user) come first.
    """
    def row(t: AgentThread, is_root: bool) -> str:
        kids = "".join(row(k, False) for k in forest.children[t.id])
        return agent_row(t, forest.tag[t.id], forest.hue[t.id], plans.get(t.id),
                         kids, is_root, now)

    roots = sorted(forest.roots, key=lambda r: not forest.busy(r))
    n = sum(1 for _ in forest.walk())
    return render("agent_tree", {
        "rows": "".join(row(r, True) for r in roots),
        "count": f"{n} thread" + ("" if n == 1 else "s"),
    })
