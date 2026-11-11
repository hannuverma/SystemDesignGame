# Devlog 05: Flaws, Bots, Verdicts, and Levels (Phase 3 & Phase 3b)

## Overview

Phases 3 and 3b introduce the core gameplay loop of **sysdesign-game**: automated adversaries attack running companies, players write and deploy fixes to vulnerable stages, and the engine judges the fix by observing external behavior and verifying that legitimate business operations remain intact.

## Architecture & Principles

### 1. Judge Behavior, Never Code
Flaw verification is completely behavior-driven. The engine never inspects or parses the player's code to detect bugs. A flaw counts as **fixed** only if:
1. The automated attack fails to achieve its exploit objective.
2. The legitimate business contract probe succeeds against the running company.

If the attack is blocked but the business contract fails, the verdict is **`broke_business`** (e.g. over-aggressive rate limiting locking out legitimate users).

### 2. Verdict Matrix
- **`vulnerable`**: The attack succeeded (impact reported > 0).
- **`fixed`**: Attack repelled AND all contract steps passed.
- **`broke_business`**: Attack repelled BUT contract steps failed.
- **`inconclusive`**: Infrastructure failure, bot crash, timeout, or insufficient requests (`< 5`). Never reported as fixed.

### 3. Visibility Principle
- **Symptoms vs. Causes**: The engine produces identical diagnostic telemetry at all difficulties. Difficulty tiers control what the UI is allowed to reveal.
- **Difficulty Tiers**:
  - `guided`: Flaw type named, endpoint and stage highlighted, lesson text shown after fix.
  - `easy`: Observable symptoms described, endpoint highlighted.
  - `normal`: No guidance text. Red status marks appear on endpoints only when observable stress (traffic spike, error spike, latency spike, or breach) exists. Unstressed flaws are never revealed.
  - `hard`: No guidance, no endpoint marks. Raw metrics and logs only.

### 4. Level & Objective Progression
Levels define a DAG of objectives with dependencies (`requires`).
- States: `locked`, `active`, `fixed`, `regressed`.
- When an objective activates, it checks for pre-emptive fixes; if not fixed, automated attack waves fire periodically.
- Deploying a fix re-evaluates the active objective and unlocks dependent objectives.
- **Regression Guard**: Background checks re-verify previously fixed objectives. If a patch re-opens a vulnerability, the objective transitions to `regressed`.
- **fail_mode & undo**:
  - `warn`: Contract failures on deploy generate warnings in the deploy response without rolling back code.
  - `POST /sessions/{id}/undo`: One-click rollback restoring the previous git commit.

