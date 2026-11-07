from __future__ import annotations

import httpx
import pytest
from engine.events.bus import EventBus
from engine.events.stats import StatsCollector
from engine.gateway.app import create_gateway_app
from engine.gateway.routes import RouteMatcher
from engine.manifest import RouteSpec
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeTargetResolver:
    def __init__(self, target_map: dict[str, str | None]) -> None:
        self.target_map = target_map

    def resolve(self, session_id: str) -> str | None:
        return self.target_map.get(session_id)


class FakeRouteResolver:
    def __init__(self, matchers: dict[str, RouteMatcher]) -> None:
        self.matchers = matchers

    def get_matcher(self, session_id: str) -> RouteMatcher | None:
        return self.matchers.get(session_id)


@pytest.fixture
def fake_upstream_app():
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def echo(request: Request, path: str):
        body = await request.body()
        return JSONResponse(
            {
                "path": f"/{path}",
                "method": request.method,
                "query": dict(request.query_params),
                "headers": dict(request.headers),
                "body": body.decode("utf-8", errors="replace"),
            }
        )

    return app


@pytest.mark.asyncio
async def test_proxy_forwards_request_and_strips_prefix(fake_upstream_app):
    upstream_transport = httpx.ASGITransport(app=fake_upstream_app)
    # Using http://upstream as base URL routed via transport
    client = httpx.AsyncClient(transport=upstream_transport, base_url="http://upstream")

    resolver = FakeTargetResolver({"s1": "http://upstream"})
    route_matcher = RouteMatcher(
        [RouteSpec(name="hello", method="POST", path="/hello")]
    )
    route_resolver = FakeRouteResolver({"s1": route_matcher})
    bus = EventBus()
    stats = StatsCollector()

    gateway_app = create_gateway_app(
        target_resolver=resolver,
        route_resolver=route_resolver,
        bus=bus,
        stats=stats,
        client=client,
    )
    sub = bus.subscribe("s1")

    gateway_transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(
        transport=gateway_transport, base_url="http://gateway"
    ) as gw:
        resp = await gw.post(
            "/s1/hello?key=val",
            content=b"test-body",
            headers={"Custom-Header": "abc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/hello"
        assert data["query"] == {"key": "val"}
        assert data["body"] == "test-body"
        assert data["headers"]["custom-header"] == "abc"
        assert "x-forwarded-for" in data["headers"]

    # Verify telemetry was recorded
    event = await sub.get()
    assert event.session_id == "s1"
    assert event.endpoint == "hello"
    assert event.status == 200
    assert event.outcome == "ok"
    assert event.bytes_in == len(b"test-body")
    sub.close()


@pytest.mark.asyncio
async def test_proxy_unresolved_target_yields_503():
    resolver = FakeTargetResolver({"s1": None})
    bus = EventBus()
    stats = StatsCollector()
    sub = bus.subscribe("s1")

    gateway_app = create_gateway_app(
        target_resolver=resolver,
        bus=bus,
        stats=stats,
    )

    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as gw:
        resp = await gw.get("/s1/hello")
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "1"

    event = await sub.get()
    assert event.status == 503
    assert event.outcome == "upstream_unavailable"
    assert event.endpoint == "unmatched"
    sub.close()


@pytest.mark.asyncio
async def test_proxy_connection_failure_yields_503():
    # Transport pointing nowhere / connection failure
    def mock_handler(request: httpx.Request):
        raise httpx.ConnectError("Connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    resolver = FakeTargetResolver({"s1": "http://dead-host"})
    bus = EventBus()
    stats = StatsCollector()
    sub = bus.subscribe("s1")

    gateway_app = create_gateway_app(
        target_resolver=resolver,
        bus=bus,
        stats=stats,
        client=client,
    )

    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as gw:
        resp = await gw.get("/s1/hello")
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "1"

    event = await sub.get()
    assert event.status == 503
    assert event.outcome == "upstream_unavailable"
    sub.close()


@pytest.mark.asyncio
async def test_proxy_timeout_yields_504():
    def mock_handler(request: httpx.Request):
        raise httpx.ReadTimeout("Timeout waiting for response")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    resolver = FakeTargetResolver({"s1": "http://slow-host"})
    bus = EventBus()
    stats = StatsCollector()
    sub = bus.subscribe("s1")

    gateway_app = create_gateway_app(
        target_resolver=resolver,
        bus=bus,
        stats=stats,
        client=client,
    )

    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as gw:
        resp = await gw.get("/s1/hello")
        assert resp.status_code == 504

    event = await sub.get()
    assert event.status == 504
    assert event.outcome == "timeout"
    sub.close()
