from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse, Response

from engine.gateway.proxy import (
    RouteResolver,
    TargetResolver,
    handle_proxy_request,
)

if TYPE_CHECKING:
    from engine.events.bus import EventBus
    from engine.events.stats import StatsCollector


def create_gateway_app(
    target_resolver: TargetResolver,
    route_resolver: RouteResolver | None = None,
    bus: EventBus | None = None,
    stats: StatsCollector | None = None,
    timeout_s: float = 5.0,
    client: httpx.AsyncClient | None = None,
    probe_token: str | None = None,
) -> FastAPI:
    """Create the gateway ASGI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=timeout_s)
            owns_client = True
        app.state.client = client
        try:
            yield
        finally:
            if owns_client and client is not None:
                await client.aclose()

    app = FastAPI(title="sysdesign-gateway", lifespan=lifespan)
    app.state.target_resolver = target_resolver
    app.state.route_resolver = route_resolver
    app.state.bus = bus
    app.state.stats = stats
    app.state.timeout_s = timeout_s
    app.state.probe_token = probe_token

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz():
        return "ok"

    @app.api_route(
        "/{session_id}/{subpath:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy_with_path(
        request: Request, session_id: str, subpath: str
    ) -> Response:
        http_client = (
            client if client is not None else getattr(app.state, "client", None)
        )
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=timeout_s)

        return await handle_proxy_request(
            request=request,
            session_id=session_id,
            subpath=subpath,
            target_resolver=app.state.target_resolver,
            client=http_client,
            route_resolver=app.state.route_resolver,
            bus=app.state.bus,
            stats=app.state.stats,
            timeout_s=app.state.timeout_s,
            probe_token=app.state.probe_token,
        )

    @app.api_route(
        "/{session_id}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy_root(request: Request, session_id: str) -> Response:
        return await proxy_with_path(request, session_id, "")

    return app
