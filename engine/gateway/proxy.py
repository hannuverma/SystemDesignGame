from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

import httpx
from starlette.requests import Request
from starlette.responses import Response

from engine.events.models import RequestEvent
from engine.gateway.routes import RouteMatcher

if TYPE_CHECKING:
    from collections.abc import Callable

    from engine.events.bus import EventBus
    from engine.events.stats import StatsCollector


class TargetResolver(Protocol):
    """Resolves a session_id to its upstream base URL (e.g. http://127.0.0.1:9000)."""

    def resolve(self, session_id: str) -> str | None: ...


class RouteResolver(Protocol):
    """Resolves a session_id to its RouteMatcher."""

    def get_matcher(self, session_id: str) -> RouteMatcher | None: ...


HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


async def handle_proxy_request(
    request: Request,
    session_id: str,
    subpath: str,
    target_resolver: TargetResolver,
    client: httpx.AsyncClient,
    route_resolver: RouteResolver | None = None,
    bus: EventBus | None = None,
    stats: StatsCollector | None = None,
    timeout_s: float = 5.0,
    clock: Callable[[], float] | None = None,
    probe_token: str | None = None,
) -> Response:
    now_fn = clock or time.time
    ts = now_fn()
    start_perf = time.perf_counter()

    if not subpath.startswith("/"):
        subpath = "/" + subpath

    method = request.method.upper()
    client_host = request.client.host if request.client else "127.0.0.1"
    is_probe = bool(probe_token and request.headers.get("x-game-probe") == probe_token)

    # Match endpoint
    endpoint = "unmatched"
    if route_resolver:
        matcher = route_resolver.get_matcher(session_id)
        if matcher:
            endpoint = matcher.match(method, subpath)

    body = await request.body()
    bytes_in = len(body)

    # Resolve upstream target
    target_base = target_resolver.resolve(session_id)
    if not target_base:
        latency_ms = (time.perf_counter() - start_perf) * 1000
        event = RequestEvent(
            session_id=session_id,
            ts=ts,
            method=method,
            path=subpath,
            endpoint=endpoint,
            status=503,
            outcome="upstream_unavailable",
            latency_ms=round(latency_ms, 2),
            upstream_ms=None,
            client=client_host,
            bytes_in=bytes_in,
            bytes_out=0,
        )
        if stats and not is_probe:
            stats.record(event)
        if bus and not is_probe:
            bus.publish(session_id, event)
        return Response(
            content=b"Upstream Unavailable",
            status_code=503,
            headers={"Retry-After": "1"},
        )

    # Forward to upstream: overwrite x-forwarded-for, strip probe token
    upstream_url = f"{target_base.rstrip('/')}{subpath}"
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "x-game-probe"
    }
    headers["x-forwarded-for"] = client_host

    upstream_start = time.perf_counter()
    upstream_ms: float | None = None
    outcome = "ok"

    try:
        resp = await client.request(
            method=method,
            url=upstream_url,
            headers=headers,
            content=body,
            params=request.query_params,
            timeout=timeout_s,
        )
        upstream_ms = (time.perf_counter() - upstream_start) * 1000
        status_code = resp.status_code
        resp_body = resp.content
        resp_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
        }
    except (httpx.ConnectError, httpx.ConnectTimeout):
        status_code = 503
        outcome = "upstream_unavailable"
        resp_body = b"Upstream Unavailable"
        resp_headers = {"Retry-After": "1"}
    except httpx.TimeoutException:
        upstream_ms = (time.perf_counter() - upstream_start) * 1000
        status_code = 504
        outcome = "timeout"
        resp_body = b"Gateway Timeout"
        resp_headers = {}
    except httpx.RequestError:
        status_code = 502
        outcome = "error"
        resp_body = b"Bad Gateway"
        resp_headers = {}

    latency_ms = (time.perf_counter() - start_perf) * 1000
    bytes_out = len(resp_body)

    event = RequestEvent(
        session_id=session_id,
        ts=ts,
        method=method,
        path=subpath,
        endpoint=endpoint,
        status=status_code,
        outcome=outcome,  # type: ignore[arg-type]
        latency_ms=round(latency_ms, 2),
        upstream_ms=round(upstream_ms, 2) if upstream_ms is not None else None,
        client=client_host,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
    )

    if stats and not is_probe:
        stats.record(event)
    if bus and not is_probe:
        bus.publish(session_id, event)

    return Response(
        content=resp_body,
        status_code=status_code,
        headers=resp_headers,
    )
