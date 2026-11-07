"""Builds the services once and hands them to routes. Tests inject a fake Runtime here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from engine.config import Settings
from engine.deploy import Deployer
from engine.events.bus import EventBus
from engine.events.stats import StatsCollector
from engine.gateway.app import create_gateway_app
from engine.gateway.proxy import RouteResolver, TargetResolver
from engine.gateway.routes import RouteMatcher
from engine.gateway.server import GatewayServer
from engine.runtime import ProcessRuntime, Runtime
from engine.workspace import Session, WorkspaceManager

logger = logging.getLogger(__name__)


class DeployerTargetResolver:
    """Resolves session_id to running runtime base URL via deployer handles."""

    def __init__(self, deployer: Deployer) -> None:
        self._deployer = deployer

    def resolve(self, session_id: str) -> str | None:
        handle = self._deployer.handle(session_id)
        return handle.base_url if handle else None


class WorkspaceRouteResolver:
    """Resolves session_id to RouteMatcher from session's manifest."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self._ws = workspace
        self._matchers: dict[str, RouteMatcher] = {}

    def get_matcher(self, session_id: str) -> RouteMatcher | None:
        if session_id in self._matchers:
            return self._matchers[session_id]
        try:
            session = self._ws.get_session(session_id)
            matcher = RouteMatcher(session.manifest.routes)
            self._matchers[session_id] = matcher
            return matcher
        except Exception:
            return None


@dataclass(frozen=True)
class Services:
    settings: Settings
    workspace: WorkspaceManager
    runtime: Runtime
    deployer: Deployer
    bus: EventBus
    stats: StatsCollector
    target_resolver: TargetResolver
    route_resolver: RouteResolver
    gateway: GatewayServer


def build_services(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    bus: EventBus | None = None,
    stats: StatsCollector | None = None,
) -> Services:
    settings = settings or Settings()
    workspace = WorkspaceManager(settings)
    runtime = runtime or ProcessRuntime(stop_grace_s=settings.stop_grace_s)
    bus = bus or EventBus()
    stats = stats or StatsCollector()
    deployer = Deployer(workspace, runtime, settings, bus=bus, stats=stats)
    target_resolver = DeployerTargetResolver(deployer)
    route_resolver = WorkspaceRouteResolver(workspace)
    gateway_app = create_gateway_app(
        target_resolver=target_resolver,
        route_resolver=route_resolver,
        bus=bus,
        stats=stats,
        timeout_s=settings.gateway_timeout_s,
    )
    gateway = GatewayServer(
        app=gateway_app,
        host="127.0.0.1",
        port=settings.gateway_port,
    )
    return Services(
        settings=settings,
        workspace=workspace,
        runtime=runtime,
        deployer=deployer,
        bus=bus,
        stats=stats,
        target_resolver=target_resolver,
        route_resolver=route_resolver,
        gateway=gateway,
    )


def get_services(conn: HTTPConnection) -> Services:
    return conn.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_session(session_id: str, services: ServicesDep) -> Session:
    return services.workspace.get_session(session_id)


SessionDep = Annotated[Session, Depends(get_session)]
