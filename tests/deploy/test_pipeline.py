"""Every deploy failure path, driven by FakeRuntime (no real processes)."""

from __future__ import annotations

import pytest
from engine.deploy import Deployer
from engine.errors import RuntimeStartError, StageNotFound

from tests.fakes import FakeRuntime

GOOD = 'def build_greeting(name: str) -> dict:\n    return {"message": f"Yo {name}"}'


def code_with(marker: str) -> str:
    return (
        "def build_greeting(name: str) -> dict:\n"
        f'    marker = "{marker}"\n'
        '    return {"m": marker}'
    )


@pytest.fixture
def fake():
    return FakeRuntime()


@pytest.fixture
def deployer(workspace, fake, settings):
    return Deployer(workspace, fake, settings)


@pytest.fixture
def session(workspace, deployer):
    s = workspace.create_session()
    deployer.launch(s)
    return s


def source(session) -> str:
    return (session.company_dir / "app/greeting.py").read_text()


def test_launch_registers_a_healthy_handle(deployer, session, fake):
    handle = deployer.handle(session.id)
    assert handle is not None and fake.healthy(handle)


def test_successful_deploy(deployer, workspace, session):
    before = workspace.head(session)
    result = deployer.deploy_stage(session, "hello", "greeting", GOOD)
    assert result.status == "deployed" and result.step is None
    assert (
        result.previous_commit == before
        and result.commit == workspace.head(session) != before
    )
    assert "Yo" in source(session)
    assert deployer.latest(session.id) == result
    assert workspace.history(session)[0]["message"] == "deploy: greeting (hello)"


def test_syntax_error_is_rejected_before_anything_changes(
    deployer, workspace, session, fake
):
    head, text, starts = workspace.head(session), source(session), fake.starts
    result = deployer.deploy_stage(
        session, "hello", "greeting", "def build_greeting(:\n    pass"
    )
    assert (result.status, result.step) == ("rejected", "validate")
    assert "line 1" in result.message
    assert workspace.head(session) == head and source(session) == text
    assert fake.starts == starts  # the company was not even restarted


@pytest.mark.parametrize(
    "code",
    [
        "def other():\n    pass",
        "x = 1",
        "def build_greeting(n):\n    pass\ndef extra():\n    pass",
    ],
)
def test_structural_problems_are_rejected_without_writing(
    deployer, workspace, session, code
):
    head, text = workspace.head(session), source(session)
    result = deployer.deploy_stage(session, "hello", "greeting", code)
    assert (result.status, result.step) == ("rejected", "write")
    assert workspace.head(session) == head and source(session) == text


@pytest.mark.parametrize(
    "marker, step",
    [("NO_START", "restart"), ("CRASH_ME", "health"), ("HEALTH_FAIL", "health")],
)
def test_runtime_failures_roll_back_to_the_last_good_version(
    deployer, workspace, session, fake, marker, step
):
    good = deployer.deploy_stage(session, "hello", "greeting", GOOD)
    assert good.status == "deployed"
    text, head = source(session), workspace.head(session)

    result = deployer.deploy_stage(session, "hello", "greeting", code_with(marker))

    assert (result.status, result.step) == ("rolled_back", step)
    assert result.commit == result.previous_commit == head
    assert source(session) == text and marker not in source(session)  # file restored
    assert workspace.head(session) == head
    assert fake.healthy(deployer.handle(session.id))  # the old version is live again
    assert result.log_tail  # the caller gets the evidence


def test_failed_deploy_leaves_no_trace_in_history(deployer, workspace, session):
    deployer.deploy_stage(session, "hello", "greeting", code_with("CRASH_ME"))
    assert [h["message"] for h in workspace.history(session)] == ["initial"]


def test_broken_when_the_rollback_also_fails(workspace, settings):
    fake = FakeRuntime(fail_starts={2, 3})  # launch=#1, the deploy=#2, the rollback=#3
    deployer = Deployer(workspace, fake, settings)
    session = workspace.create_session()
    deployer.launch(session)
    result = deployer.deploy_stage(session, "hello", "greeting", GOOD)
    assert (result.status, result.step) == ("broken", "rollback")
    assert "also failed" in result.message


def test_next_deploy_works_after_a_rollback(deployer, session):
    assert (
        deployer.deploy_stage(
            session, "hello", "greeting", code_with("CRASH_ME")
        ).status
        == "rolled_back"
    )
    assert (
        deployer.deploy_stage(session, "hello", "greeting", GOOD).status == "deployed"
    )


def test_unknown_stage_raises_instead_of_returning_a_result(deployer, session):
    with pytest.raises(StageNotFound):
        deployer.deploy_stage(session, "hello", "nope", GOOD)


def test_reset_restores_the_original_and_restarts(deployer, workspace, session):
    original = source(session)
    deployer.deploy_stage(session, "hello", "greeting", GOOD)
    result = deployer.reset(session)
    assert result.status == "deployed" and result.message == "reset to original"
    assert source(session) == original


def test_launch_failure_raises_and_cleans_up(workspace, settings):
    fake = FakeRuntime(fail_starts={1})
    deployer = Deployer(workspace, fake, settings)
    session = workspace.create_session()
    with pytest.raises(RuntimeStartError) as info:
        deployer.launch(session)
    assert info.value.log_tail
    assert deployer.handle(session.id) is None


def test_shutdown_stops_everything(deployer, session, fake):
    deployer.shutdown_all()
    assert deployer.handle(session.id) is None and fake.stops == 1
    deployer.shutdown(session.id)  # idempotent
