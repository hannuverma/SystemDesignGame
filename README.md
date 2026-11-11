# sysdesign-game

Defend a company from bots by editing its real code.

## Layout
- `engine/`   game core (api, workspace, runtime, deploy, gateway, events, flaws, bots, verify, selftest, levels)
- `content/`  data, not engine code (companies, flaws, levels)
- `web/`      dashboard + stage editor
- `runs/`     gitignored: one workspace per game session
- `docs/`     devlog posts and design notes
- `tests/`
- `scripts/`  milestone validation scripts

Principle: the engine is generic; content is data. The engine never hardcodes flaw or endpoint names.

## Features
- **Phase 1: Workspace & Deploy Pipeline** — per-session git branches, single-function stage extraction/splicing, rolling restarts, and boot health checks.
- **Phase 2: Reverse Proxy Gateway & Telemetry** — dynamic port routing, per-second request metrics, event bus (pub/sub), and real-time WebSockets.
- **Phase 3: Flaws, Attack Bots, Verdicts & Selftest**:
  - Declarative flaws (`flaw.yaml`) with standalone attack scripts and contract verification steps.
  - Subprocess bot runner with strict resource budgets and JSON-lines reporting protocol.
  - Multi-state verdicts (`vulnerable`, `fixed`, `broke_business`, `inconclusive`) judging behavior, never code.
  - Company database state reset (`/__game/reset`) before attacks and replays.
  - Flaw content verification suite (`python -m engine.selftest`).
- **Phase 3b: Levels, Objectives, Progression, Waves & Anomaly Visibility**:
  - Multi-objective levels with DAG dependencies (`content/levels/tutorial.yaml`).
  - Background wave runner, progression engine, and automatic regression detection.
  - Post-deploy contract validation with `fail_mode: warn` and one-click undo (`POST /undo`).
  - Four difficulty tiers (`guided`, `easy`, `normal`, `hard`) controlling symptom visibility vs root causes.

## Milestones
- `python scripts/milestone_c.py` — runs end-to-end flaw detection, patching, replay, and contract verification.
- `python scripts/milestone_3b.py` — runs end-to-end 3-objective level progression, fail warnings, undo, regression guard, and difficulty visibility.
- `python -m engine.selftest` — verifies baseline, reference fix, and broken fix across all flaws.

