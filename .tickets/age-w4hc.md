---
id: age-w4hc
status: closed
deps: []
links: []
created: 2026-11-14T16:53:39Z
type: bug
priority: 2
assignee: Alexis Purslane
---
# Place edits in other checkouts of the project on the map

Agents often edit files in another checkout of the same repository: another T3 worktree (~/.t3/worktrees/tertius/t0-6gej/tests/...) or a scratch copy (/tmp/t7-3e22/robust/x/src/...). t3.py drops every path outside the project root and the thread's own worktree, so a working agent gets no dot. Idea: when a path is outside the known roots, find the shortest suffix whose first two directories exist in the project's file list, and map that suffix. Seen on tertius A1.4 and A1.5 on 2026-11-14.

