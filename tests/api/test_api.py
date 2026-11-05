"""API tests with FakeRuntime, plus one end-to-end test with real processes (Milestone A)."""

from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest
from engine.api.app import create_app
from fastapi.testclient import TestClient

from tests.fakes import FakeRuntime

GOOD = 'def build_greeting(name: str) -> dict:\n    return {"message": f"Yo {name}"}'
STAGE = "/sessions/{}/stages/hello/greeting"


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, FakeRuntime())) as c:
        yield c


@pytest.fixture
def sid(client):
    response = client.post("/sessions", json={})
    assert response.status_code == 201
    return response.json()["id"]


def test_create_and_get_session(client):
    body = client.post("/sessions", json={"company": "starter"}).json()
    assert body["company"] == "starter" and body["running"] and body["healthy"]
    assert client.get(f"/sessions/{body['id']}").json()["commit"] == body["commit"]


def test_unknown_company_and_session(client):
    assert client.post("/sessions", json={"company": "nope"}).status_code == 404
    assert client.get("/sessions/aaaaaaaaaaaa").status_code == 404
    assert client.get("/sessions/..%2Fetc").status_code == 404
    assert client.get("/sessions/not-an-id/stages").status_code == 404


def test_list_and_get_stages(client, sid):
    listing = client.get(f"/sessions/{sid}/stages").json()
    assert [(s["endpoint"], s["name"]) for s in listing] == [("hello", "greeting")]
    assert "code" not in listing[0]
    detail = client.get(STAGE.format(sid)).json()
    assert detail["code"].startswith("def build_greeting")
    assert client.get(f"/sessions/{sid}/stages/hello/nope").status_code == 404


def test_put_stage_deploys(client, sid):
    response = client.put(STAGE.format(sid), json={"code": GOOD})
    assert response.status_code == 200 and response.json()["status"] == "deployed"
    assert "Yo" in client.get(STAGE.format(sid)).json()["code"]
    assert client.get(f"/sessions/{sid}/deploys/latest").json()["status"] == "deployed"
    assert (
        client.get(f"/sessions/{sid}/history")
        .json()[0]["message"]
        .startswith("deploy:")
    )


def test_put_syntax_error_is_422_and_changes_nothing(client, sid):
    before = client.get(STAGE.format(sid)).json()["code"]
    response = client.put(STAGE.format(sid), json={"code": "def build_greeting(:"})
    assert response.status_code == 422 and response.json()["status"] == "rejected"
    assert client.get(STAGE.format(sid)).json()["code"] == before


def test_put_crashing_code_is_409_and_rolled_back(client, sid):
    before = client.get(STAGE.format(sid)).json()["code"]
    code = 'def build_greeting(name: str) -> dict:\n    return {"m": "CRASH_ME"}'
    response = client.put(STAGE.format(sid), json={"code": code})
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "rolled_back" and body["step"] == "health"
    assert client.get(STAGE.format(sid)).json()["code"] == before
    assert client.get(f"/sessions/{sid}").json()["healthy"] is True


def test_put_unknown_stage_404_and_bad_body_422(client, sid):
    assert (
        client.put(
            f"/sessions/{sid}/stages/hello/nope", json={"code": GOOD}
        ).status_code
        == 404
    )
    assert client.put(STAGE.format(sid), json={}).status_code == 422


def test_latest_deploy_404_before_any_deploy(client, sid):
    assert client.get(f"/sessions/{sid}/deploys/latest").status_code == 404


def test_logs_and_reset_and_delete(client, sid):
    assert client.get(f"/sessions/{sid}/logs?tail=5").json()["lines"]
    client.put(STAGE.format(sid), json={"code": GOOD})
    reset = client.post(f"/sessions/{sid}/reset")
    assert reset.status_code == 200 and reset.json()["message"] == "reset to original"
    assert "Yo" not in client.get(STAGE.format(sid)).json()["code"]
    assert client.delete(f"/sessions/{sid}").status_code == 204
    assert client.get(f"/sessions/{sid}").status_code == 404


def test_failed_launch_cleans_up_the_session(settings):
    with TestClient(create_app(settings, FakeRuntime(fail_starts={1}))) as client:
        response = client.post("/sessions", json={})
        assert response.status_code == 500 and response.json()["log_tail"]
    assert list(settings.runs_dir.iterdir()) == []


def test_shutdown_stops_running_companies(settings):
    fake = FakeRuntime()
    with TestClient(create_app(settings, fake)) as client:
        client.post("/sessions", json={})
        assert fake.stops == 0
    assert fake.stops == 1


# ----------------------------------------------------------------------------------------
# Milestone A, automated: real processes, the way you'd do it with curl.
# ----------------------------------------------------------------------------------------


def test_milestone_a_edit_run_break_recover(settings):
    real = replace(settings, max_health_timeout_s=20.0, health_poll_interval_s=0.1)
    with TestClient(create_app(real)) as client:  # default runtime = ProcessRuntime
        created = client.post("/sessions", json={}).json()
        sid, url = created["id"], created["url"]
        assert created["healthy"]
        assert httpx.get(f"{url}/hello?name=Ada").json() == {"message": "Hello, Ada!"}

        # 1. edit a stage -> behaviour of the running company changes
        edited = client.put(STAGE.format(sid), json={"code": GOOD})
        assert edited.status_code == 200, edited.json()
        assert httpx.get(f"{url}/hello?name=Ada").json() == {"message": "Yo Ada"}

        # 2. push code that is valid Python but crashes the app on import
        crash = "@undefined_decorator\ndef build_greeting(name: str) -> dict:\n    return {}"
        response = client.put(STAGE.format(sid), json={"code": crash})
        assert response.status_code == 409, response.json()
        body = response.json()
        assert body["status"] == "rolled_back"
        assert any("undefined_decorator" in line for line in body["log_tail"])

        # 3. the previous good version is live again, same URL, file restored
        assert httpx.get(f"{url}/hello?name=Ada").json() == {"message": "Yo Ada"}
        assert "undefined_decorator" not in client.get(STAGE.format(sid)).json()["code"]
        assert client.get(f"/sessions/{sid}").json()["healthy"]

        # 4. reset goes back to the original
        assert client.post(f"/sessions/{sid}/reset").status_code == 200
        assert httpx.get(f"{url}/hello?name=Ada").json() == {"message": "Hello, Ada!"}

        pid = client.app.state.services.deployer.handle(sid).pid
    time.sleep(0.2)
    from tests.conftest import pid_alive

    assert not pid_alive(pid), "company process outlived the API"
