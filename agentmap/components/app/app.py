from datetime import datetime

from agentmap.components._template import render
from agentmap.components.agent_tree.agent_tree import agent_tree
from agentmap.components.event_log.event_log import event_log
from agentmap.components.plan_board.plan_board import plan_board
from agentmap.components.project_map.project_map import project_map
from agentmap.components.rail.rail import rail
from agentmap.components.view import Forest
from agentmap.events import Event
from agentmap.plan import Plan
from agentmap.t3 import AgentThread
from agentmap.treemap import Cell


def app(threads: list[AgentThread], plans: dict[str, Plan], cells: list[Cell],
        events: list[Event], now: datetime, project_name: str) -> str:
    """The whole `<main id="app">`: rail, map, agent tree, event log and plans.

    The server renders it on every change, and Datastar morphs it into the page.
    """
    forest = Forest(threads)
    return render("app", {
        # The rail counts the threads on the board, not the settled subagents.
        "rail": rail(project_name, list(forest.walk()), now),
        "map": project_map(cells, forest, now),
        "tree": agent_tree(forest, plans, now),
        "log": event_log(events, forest, now),
        "plans": plan_board(forest, plans, now),
    })
