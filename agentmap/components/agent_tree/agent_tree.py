from collections.abc import Collection
from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_row.agent_row import agent_row
from agentmap.components.ticket_badge.ticket_badge import ticket_badge
from agentmap.components.view import Forest, named_ids, ticket_agents, title_without
from agentmap.status import Status
from agentmap.t3 import AgentThread


def agent_tree(forest: Forest, statuses: dict[str, Status], now: datetime,
               ticket_ids: Collection[str] = frozenset()) -> str:
    """The agents module: one row per thread, each child inside its parent's row.

    Root threads with work in their tree (working, or needing the user) come first.
    A row has a badge for each ticket in `ticket_ids` that the thread's title or
    status line names, and its title drops those ids.
    """
    links = ticket_agents(forest, statuses, ticket_ids)

    def row(t: AgentThread, is_root: bool) -> str:
        kids = "".join(row(k, False) for k in forest.children[t.id])
        status = statuses.get(t.id)
        named = named_ids(t.title + " " + (status.text if status else ""), ticket_ids)
        badges = [ticket_badge(i, [forest.tag[w] for w in links[i]], forest.hue[links[i][0]],
                               f"b-{forest.tag[t.id]}") for i in named]
        return agent_row(t, forest.tag[t.id], forest.hue[t.id], status, kids, is_root, now,
                         badges=badges, title=title_without(t.title, ticket_ids))

    roots = sorted(forest.roots, key=lambda r: not forest.busy(r))
    n = sum(1 for _ in forest.walk())
    return render("agent_tree", {
        "rows": "".join(row(r, True) for r in roots),
        "count": f"{n} thread" + ("" if n == 1 else "s"),
    })
