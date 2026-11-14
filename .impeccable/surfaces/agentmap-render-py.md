---
version: 1
slug: "agentmap-render-py"
primary_target: "agentmap/render.py"
related_targets: ["agentmap/static/app.css"]
---

# agentmap dashboard (portrait side monitor)

Scope: the whole dashboard page, `agentmap/render.py` and `agentmap/static/app.css`. Mode: Operate, watched rather than operated.

Audience and job: one developer glances at a 1080 × 1920 portrait monitor in bright daylight to see where agents work, what just changed, and what needs an answer; sometimes reads for 10 to 30 seconds.

Constraints: keep the map, the ownership tree, the plans, the states and the hover link. Add the running log. Plans move out of the cards into their own module. No "NEW" badges.

## Direction contract

THESIS: A packed board of hairline modules where every mark is inked by its age. Fresh activity is black and red, older activity fades toward pencil gray, and each agent's recent path across the project is a route line that fades along its length. It refuses the dark stack of cards with badges.

OWN-WORLD: White ground, black compact gothic sans (Hiragino Sans), cool gray hairline modules packed edge to edge, each headed by a small utility-red tab. Red also marks "needs you". Each agent has its own hue for its tag, map dot and route line. One five-step ink ramp (now, 1 min, 5 min, 15 min, 1 h) is the only age signal.

STORY: A glance shows where activity is (bright route ends on the map), what needs the user (red), and what just happened (black lines at the top of the log). A read gives the agent tree, the plans and the log in detail.

FIRST VIEWPORT: The whole 1080 × 1920 screen. A rail of 64 px: red project tab, state counts, clock. A full-width map module of about 560 px with route lines and tagged dots. A middle band: agent tree module two thirds wide, events log one third wide, both filling the height. A full-width plans module at the bottom with two columns.

FORM: Japanese high-density web (challenger japanese-high-density-web), fused with the route lines of the Route map direction and the ink ramp of the Watch log direction. seed:147b6fe7963b060c

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
