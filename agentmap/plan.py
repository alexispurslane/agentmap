"""The plan store: the goals, status lines and todo lists that agent threads post.

The source of truth is an append-only log with one JSON object per line. The
in-memory plans are the fold of that log. Every change goes through the same
two pure steps as a replay: `_normalize` checks the shape of a record and
strips its texts, and `_apply` gives the plan after the record.

Invariants, which `PlanStore.check_invariants` tests in every build:

1. In each plan, top-level ids are ``1..n`` without gaps, and the children of
   item ``k`` are ``k.1..k.m`` without gaps.
2. No item is deeper than two levels.
3. Each leaf state is one of ``todo``, ``doing``, ``done``.
4. Each parent state equals the state derived from its children.
5. A fresh replay of the log gives the same plans as the memory.
6. A fresh replay of the log gives the same events as the memory.

Times: every time in a plan is the `ts` of a log record, so a replay gives
the same times. A leaf's `changed_at` is the time of its creation or of its
latest state change. A parent's `changed_at` is the latest `changed_at` of
itself and its children.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys
import threading
from collections import deque
from datetime import datetime, timezone

from agentmap.events import Event

GOAL_MAX = 500
STATUS_MAX = 160
ITEM_MAX = 200
# events() gives at most this many events.
EVENTS_MAX = 200

LEAF_STATES = ("todo", "doing", "done")
MARK_STATES = ("doing", "done")
_RANK = {state: rank for rank, state in enumerate(LEAF_STATES)}
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class PlanError(ValueError):
    """A rejected request. The message is shown to the agent."""


@dataclasses.dataclass(frozen=True)
class Item:
    id: str
    text: str
    state: str
    children: tuple[Item, ...]
    changed_at: datetime


@dataclasses.dataclass(frozen=True)
class Plan:
    goal: str | None
    status: str | None
    items: tuple[Item, ...]
    updated_at: datetime
    status_at: datetime | None  # The time of the latest status line, or None.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def derived_state(children: tuple[Item, ...]) -> str:
    """The state of an item with these children."""
    if all(child.state == "done" for child in children):
        return "done"
    if any(child.state != "todo" for child in children):
        return "doing"
    return "todo"


class PlanStore:
    def __init__(self, log_path: pathlib.Path):
        self._path = pathlib.Path(log_path)
        self._lock = threading.Lock()
        self._plans, self._events = _replay(self._path, warn=True)

    def set_goal(self, thread: str, text: str) -> None:
        self._change({"thread": thread, "op": "goal", "text": text})

    def set_status(self, thread: str, text: str) -> None:
        self._change({"thread": thread, "op": "status", "text": text})

    def add_items(self, thread: str, texts: list[str], parent: str | None = None,
                  children: list[list[str]] | None = None) -> list[str]:
        """Add items and return their ids in outline order."""
        old, new = self._change({"thread": thread, "op": "add", "parent": parent,
                                 "texts": texts, "children": children})
        known = set(_ids(old))
        return [item_id for item_id in _ids(new) if item_id not in known]

    def mark(self, thread: str, item_id: str, state: str) -> None:
        self._change({"thread": thread, "op": "mark", "id": item_id, "state": state})

    def plan(self, thread: str) -> Plan | None:
        with self._lock:
            return self._plans.get(thread)

    def plans(self) -> dict[str, Plan]:
        with self._lock:
            return dict(self._plans)

    def events(self) -> list[Event]:
        """The newest EVENTS_MAX events of all threads, newest first."""
        with self._lock:
            return list(reversed(self._events))

    def check_invariants(self) -> None:
        """Raise AssertionError when an invariant of the module docstring is broken."""
        with self._lock:
            plans, events = dict(self._plans), list(self._events)
            replayed_plans, replayed_events = _replay(self._path, warn=False)
        problems = [f"thread {thread}: {problem}"
                    for thread, plan in sorted(plans.items())
                    for problem in _violations(plan)]
        if replayed_plans != plans:
            problems.append("a fresh replay of the log differs from the memory")
        if list(replayed_events) != events:
            problems.append("the events of a fresh replay of the log differ from the memory")
        if problems:
            raise AssertionError("; ".join(problems))

    def _change(self, request: dict) -> tuple[Plan | None, Plan]:
        """Apply one request: log it, then store the new plan. Return the old and new plan."""
        with self._lock:
            record = {"ts": _now().strftime(_TS_FORMAT), **_normalize(request)}
            thread = record["thread"]
            old = self._plans.get(thread)
            new = _apply(old, record)
            if new is old:
                return old, new
            assert not _violations(new), _violations(new)
            self._append(record)
            self._plans[thread] = new
            self._events.extend(_events(record, new))
            assert set(_ids(old)) <= set(_ids(new)), "an item id disappeared"
            return old, new

    def _append(self, record: dict) -> None:
        assert self._lock.locked()
        line = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a+b") as log:
            if log.seek(0, 2) > 0:
                log.seek(-1, 2)
                if log.read(1) != b"\n":
                    # The last line was torn, for example by a crash. Do not
                    # join this record to it.
                    line = b"\n" + line
            log.write(line)
            log.flush()


def _replay(path: pathlib.Path, warn: bool) -> tuple[dict[str, Plan], deque[Event]]:
    """The plans and the newest EVENTS_MAX events, oldest first, that the log gives."""
    plans: dict[str, Plan] = {}
    events: deque[Event] = deque(maxlen=EVENTS_MAX)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return plans, events
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8"))
            if not isinstance(entry, dict):
                raise PlanError("not a JSON object")
            record = {"ts": entry["ts"], **_normalize(entry)}
            thread = record["thread"]
            old = plans.get(thread)
            new = _apply(old, record)
            if new is not old:
                plans[thread] = new
                events.extend(_events(record, new))
        except (ValueError, KeyError, TypeError) as error:
            if warn:
                print(f"agentmap: {path}:{number}: skipped log line: {error!r}",
                      file=sys.stderr)
    return plans, events


def _text(value, what: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PlanError(f"The {what} must be a string.")
    text = value.strip()
    if not text:
        raise PlanError(f"The {what} is empty.")
    if len(text) > limit:
        raise PlanError(f"The {what} has {len(text)} characters; the limit is {limit}.")
    return text


def _item_id(value) -> str:
    if not isinstance(value, str):
        raise PlanError("An item id must be a string such as \"2\" or \"2.1\".")
    return value.strip()


def _normalize(request: dict) -> dict:
    """Check the shape of one request or log record, and strip its texts."""
    thread = request["thread"]
    if not isinstance(thread, str) or not thread:
        raise PlanError("The thread id is empty.")
    op = request["op"]
    if op == "goal":
        return {"thread": thread, "op": op, "text": _text(request["text"], "goal", GOAL_MAX)}
    if op == "status":
        return {"thread": thread, "op": op,
                "text": _text(request["text"], "status line", STATUS_MAX)}
    if op == "add":
        texts = request["texts"]
        if not isinstance(texts, list) or not texts:
            raise PlanError("Give a non-empty list of item texts.")
        texts = [_text(text, "item text", ITEM_MAX) for text in texts]
        parent = request["parent"]
        children = request["children"]
        if parent is not None:
            parent = _item_id(parent)
            if children is not None:
                raise PlanError("Items added under a parent cannot have children: "
                                "a plan has two levels only.")
        if children is not None:
            if (not isinstance(children, list) or len(children) != len(texts)
                    or not all(isinstance(group, list) for group in children)):
                raise PlanError("Give one list of child texts per item.")
            children = [[_text(text, "item text", ITEM_MAX) for text in group]
                        for group in children]
        return {"thread": thread, "op": op, "parent": parent, "texts": texts,
                "children": children}
    if op == "mark":
        state = request["state"]
        if state not in MARK_STATES:
            raise PlanError(f"The state must be \"doing\" or \"done\", not {state!r}.")
        return {"thread": thread, "op": op, "id": _item_id(request["id"]), "state": state}
    raise PlanError(f"Unknown operation {op!r}.")


def _apply(plan: Plan | None, record: dict) -> Plan:
    """The plan after one normalized record. Return `plan` itself when nothing changes."""
    ts = _time(record)
    base = plan or Plan(goal=None, status=None, items=(), updated_at=ts, status_at=None)
    op = record["op"]
    if op == "goal":
        return dataclasses.replace(base, goal=record["text"], updated_at=ts)
    if op == "status":
        return dataclasses.replace(base, status=record["text"], updated_at=ts, status_at=ts)
    if op == "add":
        items = _add(base.items, record["parent"], record["texts"], record["children"], ts)
        return dataclasses.replace(base, items=items, updated_at=ts)
    items = _mark(base.items, record["id"], record["state"], ts)
    if items is base.items:
        return base
    return dataclasses.replace(base, items=items, updated_at=ts)


def _time(record: dict) -> datetime:
    return datetime.strptime(record["ts"], _TS_FORMAT).replace(tzinfo=timezone.utc)


def _events(record: dict, plan: Plan) -> list[Event]:
    """The events of one record that changed a plan into `plan`, in outline order."""
    ts, thread, op = _time(record), record["thread"], record["op"]
    if op in ("goal", "status"):
        return [Event(ts, thread, op, record["text"])]
    if op == "add":
        groups = record["children"] or [[] for _ in record["texts"]]
        texts = [text for text, group in zip(record["texts"], groups) for text in [text, *group]]
        return [Event(ts, thread, "added", text) for text in texts]
    item = next(i for top in plan.items for i in (top, *top.children) if i.id == record["id"])
    return [Event(ts, thread, record["state"], item.text)]


def _parent(item: Item, kids: tuple[Item, ...]) -> Item:
    """`item` with the children `kids`, its derived state and the latest changed_at."""
    changed_at = max(item.changed_at, *(kid.changed_at for kid in kids))
    return Item(item.id, item.text, derived_state(kids), kids, changed_at)


def _add(items: tuple[Item, ...], parent: str | None, texts: list[str],
         children: list[list[str]] | None, ts: datetime) -> tuple[Item, ...]:
    if parent is None:
        groups = children or [[] for _ in texts]
        new = []
        for number, (text, group) in enumerate(zip(texts, groups), start=len(items) + 1):
            kids = tuple(Item(f"{number}.{n}", kid, "todo", (), ts)
                         for n, kid in enumerate(group, start=1))
            new.append(Item(str(number), text, "todo", kids, ts))
        return items + tuple(new)
    if "." in parent:
        raise PlanError(f"Item {parent} is a child item. A plan has two levels only, "
                        "so give a top-level item as the parent.")
    position = next((n for n, item in enumerate(items) if item.id == parent), None)
    if position is None:
        raise PlanError(f"There is no top-level item {parent}.")
    item = items[position]
    if item.state == "done":
        raise PlanError(f"Item {parent} is done, so it gets no new children. "
                        "Add a new top-level item instead.")
    kids = item.children + tuple(Item(f"{parent}.{n}", text, "todo", (), ts)
                                 for n, text in enumerate(texts, start=len(item.children) + 1))
    return items[:position] + (_parent(item, kids),) + items[position + 1:]


def _mark(items: tuple[Item, ...], item_id: str, state: str,
          ts: datetime) -> tuple[Item, ...]:
    top_id = item_id.split(".")[0]
    position = next((n for n, item in enumerate(items) if item.id == top_id), None)
    if position is None:
        raise PlanError(f"There is no item {item_id}.")
    top = items[position]
    if top_id == item_id:
        if top.children:
            raise PlanError(f"Item {item_id} has children. Its state follows them, "
                            "so mark the children instead.")
        changed = _move(top, state, ts)
    else:
        kid_position = next((n for n, kid in enumerate(top.children) if kid.id == item_id),
                            None)
        if kid_position is None:
            raise PlanError(f"There is no item {item_id}.")
        kid = _move(top.children[kid_position], state, ts)
        if kid is top.children[kid_position]:
            return items
        kids = top.children[:kid_position] + (kid,) + top.children[kid_position + 1:]
        changed = _parent(top, kids)
    if changed is top:
        return items
    return items[:position] + (changed,) + items[position + 1:]


def _move(leaf: Item, state: str, ts: datetime) -> Item:
    if leaf.state == state:
        return leaf
    if _RANK[state] < _RANK[leaf.state]:
        raise PlanError(f"Item {leaf.id} is {leaf.state}; a state never moves back.")
    return Item(leaf.id, leaf.text, state, (), ts)


def _ids(plan: Plan | None) -> list[str]:
    if plan is None:
        return []
    return [i for item in plan.items for i in [item.id] + [kid.id for kid in item.children]]


def _violations(plan: Plan) -> list[str]:
    """The structural invariants (1 to 4) that one plan breaks."""
    problems = []
    if [item.id for item in plan.items] != [str(n) for n in range(1, len(plan.items) + 1)]:
        problems.append("top-level ids are not 1..n")
    for item in plan.items:
        expected = [f"{item.id}.{n}" for n in range(1, len(item.children) + 1)]
        if [kid.id for kid in item.children] != expected:
            problems.append(f"child ids of item {item.id} are not {item.id}.1..m")
        for kid in item.children:
            if kid.children:
                problems.append(f"item {kid.id} has children: deeper than two levels")
        for leaf in [item] if not item.children else item.children:
            if leaf.state not in LEAF_STATES:
                problems.append(f"item {leaf.id} has leaf state {leaf.state!r}, "
                                "not todo, doing or done")
        if item.children and item.state != derived_state(item.children):
            problems.append(f"item {item.id} has state {item.state!r}, but its derived "
                            f"state is {derived_state(item.children)!r}")
    return problems
