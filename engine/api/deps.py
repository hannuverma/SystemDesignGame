"""Builds the services once and hands them to routes. Tests inject a fake Runtime here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from engine.config import Settings
from engine.deploy import Deployer
from engine.economy.manager import EconomyManager
from engine.events.bus import EventBus
from engine.events.stats import StatsCollector
from engine.flaws.loader import load_all_flaws
from engine.gateway.app import create_gateway_app
from engine.gateway.proxy import RouteResolver, TargetResolver
from engine.gateway.routes import RouteMatcher
from engine.gateway.server import GatewayServer
from engine.levels.loader import load_all_levels
from engine.levels.runner import LevelRunner
from engine.manifest import load_manifest
from engine.runtime import ProcessRuntime, Runtime
from engine.verify.attacks import AttackManager
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
    probe_token: str
    attacks: AttackManager
    levels_runner: LevelRunner
    economy: EconomyManager


def build_services(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    bus: EventBus | None = None,
    stats: StatsCollector | None = None,
    probe_token: str | None = None,
) -> Services:
    import secrets

    settings = settings or Settings()
    workspace = WorkspaceManager(settings)
    runtime = runtime or ProcessRuntime(stop_grace_s=settings.stop_grace_s)
    bus = bus or EventBus()
    stats = stats or StatsCollector()
    probe_token = probe_token or secrets.token_hex(16)
    deployer = Deployer(workspace, runtime, settings, bus=bus, stats=stats)
    target_resolver = DeployerTargetResolver(deployer)
    route_resolver = WorkspaceRouteResolver(workspace)
    gateway_app = create_gateway_app(
        target_resolver=target_resolver,
        route_resolver=route_resolver,
        bus=bus,
        stats=stats,
        timeout_s=settings.gateway_timeout_s,
        probe_token=probe_token,
    )
    gateway = GatewayServer(
        app=gateway_app,
        host="127.0.0.1",
        port=settings.gateway_port,
    )
    attacks = AttackManager()
    company_dir = settings.content_dir / "companies" / "starter"
    manifest = (
        load_manifest(company_dir) if (company_dir / "company.yaml").is_file() else None
    )
    flaws = (
        load_all_flaws(settings.content_dir / "flaws", manifest, company_dir)
        if manifest
        else {}
    )
    levels = load_all_levels(
        settings.content_dir / "levels",
        flaws=flaws,
        companies_dir=settings.content_dir / "companies",
    )
    levels_runner = LevelRunner(
        flaws=flaws,
        levels=levels,
        gateway_url_fn=lambda sid: f"{gateway.url}/{sid}",
        probe_token=probe_token,
        bus=bus,
        reset_fn=lambda s: deployer.reset_company_state(s),
    )
    deployer.set_post_deploy_hook(levels_runner.on_post_deploy_hook)
    economy = EconomyManager(bus=bus)

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
        probe_token=probe_token,
        attacks=attacks,
        levels_runner=levels_runner,
        economy=economy,
    )


def get_services(conn: HTTPConnection) -> Services:
    return conn.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_session(session_id: str, services: ServicesDep) -> Session:
    return services.workspace.get_session(session_id)


SessionDep = Annotated[Session, Depends(get_session)]
