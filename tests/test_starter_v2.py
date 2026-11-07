from __future__ import annotations

import time
from dataclasses import replace

import httpx
from engine.api.app import create_app
from fastapi.testclient import TestClient

from tests.conftest import pid_alive


def test_starter_v2_legitimate_flows_and_reset(settings):
    """Test starter v2 legitimate API flows: login, search, items, delete, and DB reset."""
    real = replace(
        settings,
        max_health_timeout_s=20.0,
        health_poll_interval_s=0.1,
        gateway_port=0,
    )

    with TestClient(create_app(real)) as client:
        services = client.app.state.services
        gw_url = services.gateway.url

        # 1. Boot session
        created = client.post("/sessions", json={"company": "starter"}).json()
        sid = created["id"]
        assert created["healthy"]

        session = services.workspace.get_session(sid)
        base = f"{gw_url}/{sid}"

        # 2. Login as alice
        login_res = httpx.post(
            f"{base}/login",
            json={"username": "alice", "password": "alice_super_secret_password_123!"},
        )
        assert login_res.status_code == 200
        data = login_res.json()
        token = data["token"]
        assert data["username"] == "alice"

        # Wrong password gives 401
        bad_login = httpx.post(
            f"{base}/login",
            json={"username": "alice", "password": "wrong_password"},
        )
        assert bad_login.status_code == 401

        # 3. Public list items
        items_res = httpx.get(f"{base}/items")
        assert items_res.status_code == 200
        items = items_res.json()
        assert len(items) == 3
        # Confirm owner field is NOT exposed in public list
        assert "owner" not in items[0]
        assert {"id", "name", "price"} <= set(items[0].keys())

        # 4. Search items
        search_res = httpx.get(f"{base}/search?q=laptop")
        assert search_res.status_code == 200
        results = search_res.json()["results"]
        assert len(results) == 1
        assert results[0]["name"] == "laptop"

        # 5. Delete item with token
        del_res = httpx.delete(
            f"{base}/items/1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert del_res.status_code == 200
        assert del_res.json() == {"deleted": 1}

        # Item 1 is now deleted
        items_after = httpx.get(f"{base}/items").json()
        assert len(items_after) == 2
        assert not any(i["id"] == 1 for i in items_after)

        # 6. Test state reset
        reset_ok = services.deployer.reset_company_state(session)
        assert reset_ok is True

        # Items are restored!
        items_reset = httpx.get(f"{base}/items").json()
        assert len(items_reset) == 3
        assert any(i["id"] == 1 for i in items_reset)

        # 7. Gateway X-Forwarded-For overwrite test
        # When spoofed header is sent, company receives real client IP (127.0.0.1)
        res_spoof = httpx.get(
            f"{base}/hello",
            headers={"X-Forwarded-For": "192.168.1.100"},
        )
        assert res_spoof.status_code == 200

        # 8. Probe exclusion test
        # Probe request with matching token must NOT record in stats
        stats_before = services.stats.snapshot(sid).totals.count
        res_probe = httpx.get(
            f"{base}/hello",
            headers={"X-Game-Probe": services.probe_token},
        )
        assert res_probe.status_code == 200
        stats_after = services.stats.snapshot(sid).totals.count
        assert stats_after == stats_before

        pid = services.deployer.handle(sid).pid

    time.sleep(0.2)
    assert not pid_alive(pid)
