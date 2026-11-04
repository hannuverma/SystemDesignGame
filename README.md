# sysdesign-game

Defend a company from bots by editing its real code.

## Layout
- engine/   game core (api, workspace, runtime, gateway, bots, economy, levels, events)
- content/  data, not engine code (companies, flaws, levels)
- web/      dashboard + stage editor
- runs/     gitignored: one workspace per game session
- docs/     devlog posts and design notes
- tests/

Principle: the engine is generic; content is data. The engine never hardcodes "login".
