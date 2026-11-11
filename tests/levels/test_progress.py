from __future__ import annotations

from engine.config import REPO_ROOT
from engine.levels.loader import load_level
from engine.levels.progress import (
    init_level_progress,
    load_progress,
    save_progress,
    update_objective_verdict,
)
from engine.verify.verdict import Verdict

TUTORIAL_PATH = REPO_ROOT / "content/levels/tutorial.yaml"


def test_progress_lifecycle_and_transitions() -> None:
    level = load_level(TUTORIAL_PATH)
    progress = init_level_progress(level)

    # 1. Initial state
    assert progress.level_id == "tutorial"
    assert progress.is_completed is False
    assert progress.active_objective_id == "obj_rate_limit"
    assert progress.objectives["obj_rate_limit"].status == "active"
    assert progress.objectives["obj_sql_injection"].status == "locked"
    assert progress.objectives["obj_missing_ownership"].status == "locked"

    fixed_verdict = Verdict(status="fixed", reasons=["Passed"])
    vuln_verdict = Verdict(status="vulnerable", reasons=["Failed"])

    # 2. Complete objective 1
    t1 = update_objective_verdict(progress, level, "obj_rate_limit", fixed_verdict)
    assert len(t1) == 2
    assert t1[0].event == "completed"
    assert t1[0].objective_id == "obj_rate_limit"
    assert t1[1].event == "activated"
    assert t1[1].objective_id == "obj_sql_injection"

    assert progress.objectives["obj_rate_limit"].status == "fixed"
    assert progress.objectives["obj_sql_injection"].status == "active"
    assert progress.active_objective_id == "obj_sql_injection"

    # 3. Complete objective 2
    t2 = update_objective_verdict(progress, level, "obj_sql_injection", fixed_verdict)
    assert len(t2) == 2
    assert t2[0].event == "completed"
    assert t2[1].event == "activated"
    assert t2[1].objective_id == "obj_missing_ownership"

    assert progress.objectives["obj_sql_injection"].status == "fixed"
    assert progress.objectives["obj_missing_ownership"].status == "active"
    assert progress.active_objective_id == "obj_missing_ownership"

    # 4. Regression: re-introduce vulnerability in objective 1
    t_reg = update_objective_verdict(progress, level, "obj_rate_limit", vuln_verdict)
    assert len(t_reg) == 1
    assert t_reg[0].event == "regressed"
    assert t_reg[0].objective_id == "obj_rate_limit"
    assert progress.objectives["obj_rate_limit"].status == "regressed"
    assert progress.active_objective_id == "obj_rate_limit"

    # 5. Fix regressed objective 1
    t_fix_reg = update_objective_verdict(
        progress, level, "obj_rate_limit", fixed_verdict
    )
    assert any(t.event == "completed" for t in t_fix_reg)
    assert progress.objectives["obj_rate_limit"].status == "fixed"

    # 6. Complete final objective 3
    t3 = update_objective_verdict(
        progress, level, "obj_missing_ownership", fixed_verdict
    )
    assert any(t.event == "level_completed" for t in t3)
    assert progress.is_completed is True
    assert progress.active_objective_id is None


def test_progress_persistence_roundtrip(tmp_path) -> None:
    level = load_level(TUTORIAL_PATH)
    progress = init_level_progress(level)
    progress.objectives["obj_rate_limit"].hints_used = 2

    p_file = tmp_path / "progress.json"
    save_progress(progress, p_file)
    assert p_file.is_file()

    loaded = load_progress(p_file)
    assert loaded.level_id == progress.level_id
    assert loaded.active_objective_id == progress.active_objective_id
    assert loaded.objectives["obj_rate_limit"].hints_used == 2
    assert loaded.objectives["obj_rate_limit"].status == "active"
