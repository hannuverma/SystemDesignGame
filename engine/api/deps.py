"""Builds the services once and hands them to routes. Tests inject a fake Runtime here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from engine.config import Settings
from engine.deploy import Deployer
from engine.runtime import ProcessRuntime, Runtime
from engine.workspace import Session, WorkspaceManager


@dataclass(frozen=True)
class Services:
    settings: Settings
    workspace: WorkspaceManager
    runtime: Runtime
    deployer: Deployer


def build_services(
    settings: Settings | None = None, runtime: Runtime | None = None
) -> Services:
    settings = settings or Settings()
    workspace = WorkspaceManager(settings)
    runtime = runtime or ProcessRuntime(stop_grace_s=settings.stop_grace_s)
    return Services(settings, workspace, runtime, Deployer(workspace, runtime, settings))


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def get_session(session_id: str, services: ServicesDep) -> Session:
    return services.workspace.get_session(session_id)


SessionDep = Annotated[Session, Depends(get_session)]
