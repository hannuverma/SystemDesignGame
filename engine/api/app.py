from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from engine import errors
from engine.api.deps import build_services
from engine.api.routes import deploys, sessions, stages
from engine.config import Settings
from engine.runtime import Runtime

# The single place where typed engine errors become HTTP status codes.
STATUS_BY_ERROR: dict[type[Exception], int] = {
    errors.SessionNotFound: 404,
    errors.StageNotFound: 404,
    errors.CompanyNotFound: 404,
    errors.PathEscape: 400,
    errors.InvalidStageCode: 422,
    errors.StageParseError: 500,
    errors.ManifestError: 500,
    errors.GitError: 500,
    errors.RuntimeStartError: 500,
}


def create_app(
    settings: Settings | None = None, runtime: Runtime | None = None
) -> FastAPI:
    services = build_services(settings, runtime)

    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        services.deployer.shutdown_all()  # never leave company processes behind

    app = FastAPI(title="sysdesign-game engine", lifespan=lifespan)
    app.state.services = services

    @app.exception_handler(errors.GameError)
    async def game_error_handler(_: Request, exc: errors.GameError) -> JSONResponse:
        status = next(
            (STATUS_BY_ERROR[t] for t in type(exc).__mro__ if t in STATUS_BY_ERROR), 500
        )
        body: dict = {"error": type(exc).__name__, "detail": str(exc)}
        if isinstance(exc, errors.RuntimeStartError):
            body["log_tail"] = exc.log_tail
        return JSONResponse(body, status_code=status)

    app.include_router(sessions.router)
    app.include_router(stages.router)
    app.include_router(deploys.router)
    return app
