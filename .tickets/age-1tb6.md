---
id: age-1tb6
status: closed
deps: []
links: []
created: 2026-11-14T16:42:49Z
type: feature
priority: 2
assignee: Alexis Purslane
---
# Hide settled subagents from map, tree and plans

A subagent thread is settled when its newest delegation record in orchestration_v2_projection_subagents is not running and the thread is not working or needs_you. Settled threads leave the map, the agent tree and the plans board, unless a descendant is visible. Failed delegations stay visible. Tags and hues stay computed from all threads; the event log keeps settled threads. Roots never settle.

