from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth import router as auth_router
from .api.datasets import router as datasets_router
from .api.dependencies import require_session
from .api.evaluations import router as evaluations_router
from .api.health import router as health_router
from .api.overview import router as overview_router
from .api.runners import router as runners_router
from .config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.environment.lower() in {"production", "prod"} and not settings.auth_session_secret:
        raise RuntimeError(
            "AUTH_SESSION_SECRET is required in production; refusing to start with the "
            "development fallback"
        )
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    # Console APIs require an authenticated session. The runner plane router is
    # included without this dependency because it authenticates its own
    # short-lived tokens and heartbeat credentials.
    app.include_router(overview_router, dependencies=[Depends(require_session)])
    app.include_router(runners_router)
    app.include_router(evaluations_router, dependencies=[Depends(require_session)])
    app.include_router(datasets_router, dependencies=[Depends(require_session)])
    return app


app = create_app()
