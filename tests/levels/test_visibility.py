from __future__ import annotations

from engine.config import REPO_ROOT
from engine.flaws.loader import load_all_flaws
from engine.levels.loader import load_level
from engine.levels.progress import init_level_progress
from engine.levels.visibility import filter_endpoints_view, filter_level_view
from engine.manifest import load_manifest

STARTER_DIR = REPO_ROOT / "content/companies/starter"
FLAWS_DIR = REPO_ROOT / "content/flaws"
TUTORIAL_PATH = REPO_ROOT / "content/levels/tutorial.yaml"


def test_visibility_guided_tier() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaws = load_all_flaws(FLAWS_DIR, manifest, STARTER_DIR)
    level = load_level(TUTORIAL_PATH, flaws=flaws)
    progress = init_level_progress(level)

    view = filter_level_view(level, progress, flaws, difficulty="guided")
    obj0 = view.objectives[0]
    assert obj0.title is not None
    assert "rate limiting" in (obj0.text or "")
    assert obj0.flaw_category == "authentication"
    assert obj0.endpoint_highlight == "login"
    assert obj0.stage_highlight == "rate_limit"

    # Endpoints on guided
    ep_statuses = {
        "login": ("warning", ["401 surge"]),
        "search": ("ok", []),
    }
    endpoints_view = filter_endpoints_view(
        ep_statuses, active_flaw=flaws["brute_force_login"], difficulty="guided"
    )
    login_ep = next(e for e in endpoints_view if e.endpoint == "login")
    assert login_ep.highlighted is True
    assert login_ep.stage_highlight == "rate_limit"


def test_visibility_easy_tier() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaws = load_all_flaws(FLAWS_DIR, manifest, STARTER_DIR)
    level = load_level(TUTORIAL_PATH, flaws=flaws)
    progress = init_level_progress(level)

    view = filter_level_view(level, progress, flaws, difficulty="easy")
    obj0 = view.objectives[0]
    assert obj0.title is not None
    assert obj0.endpoint_highlight == "login"
    # Stage highlight is HIDDEN on easy
    assert obj0.stage_highlight is None

    ep_statuses = {
        "login": ("warning", ["401 surge"]),
        "search": ("ok", []),
    }
    endpoints_view = filter_endpoints_view(
        ep_statuses, active_flaw=flaws["brute_force_login"], difficulty="easy"
    )
    login_ep = next(e for e in endpoints_view if e.endpoint == "login")
    assert login_ep.highlighted is True
    assert login_ep.stage_highlight is None


def test_visibility_normal_tier_never_reveals_unobserved_flaws() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaws = load_all_flaws(FLAWS_DIR, manifest, STARTER_DIR)
    level = load_level(TUTORIAL_PATH, flaws=flaws)
    progress = init_level_progress(level)

    view = filter_level_view(level, progress, flaws, difficulty="normal")
    obj0 = view.objectives[0]
    # No stage highlight and no endpoint highlight in objective view
    assert obj0.endpoint_highlight is None
    assert obj0.stage_highlight is None

    # Endpoints on normal: only endpoints with symptoms (!= ok) appear!
    ep_statuses = {
        "login": ("warning", ["flood"]),
        "search": (
            "ok",
            [],
        ),  # Even though search has SQLi in code, no symptoms observed yet!
        "items": ("ok", []),
    }
    endpoints_view = filter_endpoints_view(
        ep_statuses, active_flaw=flaws["brute_force_login"], difficulty="normal"
    )
    # Only login is returned; search is NOT marked because it is symptom-free!
    assert len(endpoints_view) == 1
    assert endpoints_view[0].endpoint == "login"
    assert endpoints_view[0].highlighted is False
    assert endpoints_view[0].status == "warning"


def test_visibility_hard_tier_returns_no_marks() -> None:
    manifest = load_manifest(STARTER_DIR)
    flaws = load_all_flaws(FLAWS_DIR, manifest, STARTER_DIR)
    level = load_level(TUTORIAL_PATH, flaws=flaws)
    progress = init_level_progress(level)

    view = filter_level_view(level, progress, flaws, difficulty="hard")
    assert view.objectives[0].title is None
    assert view.objectives[0].text is None

    ep_statuses = {
        "login": ("critical", ["breached"]),
    }
    endpoints_view = filter_endpoints_view(
        ep_statuses, active_flaw=flaws["brute_force_login"], difficulty="hard"
    )
    # Hard tier hides all endpoint marks!
    assert endpoints_view == []
