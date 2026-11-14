---
id: age-ux0m
status: closed
deps: []
links: []
created: 2026-11-14T15:51:50Z
type: task
priority: 2
assignee: Alexis Purslane
parent: age-521d
---
# Build HTML with t-strings that escape by default

Replace the f-string HTML and the manual escape calls in render.py with t-strings and one helper, agentmap/markup.py html(), that escapes every value except Safe. Output must stay byte for byte the same. Design doc: docs/plans/agentmap.md, section render.py.

