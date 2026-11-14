from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.agent_assets import router as agent_assets_router
from .api.auth import router as auth_router
from .api.dataset_image_sources import router as dataset_image_sources_router
from .api.datasets import router as datasets_router
from .api.dependencies import require_session
from .api.deployments import router as deployments_router
from .api.eval_workbench import router as eval_workbench_router
from .api.evaluations import router as evaluations_router
from .api.health import router as health_router
from .api.model_configs import router as model_configs_router
from .api.model_gateway import router as model_gateway_router
from .api.operations import router as operations_router
from .api.overview import router as overview_router
from .api.resources import router as resources_router
from .api.results import router as results_router
from .api.runners import router as runners_router
from .api.runtimes import router as runtimes_router
from .api.traces import router as traces_router
from .config import get_settings
from .services.provisioning import register_builtin_provisioners


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # M4-04: the API process answers connection verification and the
    # cost-confirmed provisioning test, so it needs the same provider registry
    # as the worker. Registration performs no network call.
    register_builtin_provisioners(get_settings())
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.environment.lower() in {"production", "prod"}:
        if not settings.auth_session_secret:
            raise RuntimeError(
                "AUTH_SESSION_SECRET is required in production; refusing to start with the "
                "development fallback"
            )
        if not settings.secret_box_key:
            raise RuntimeError("SECRET_BOX_KEY is required in production")
        if not settings.gateway_token_secret:
            raise RuntimeError("GATEWAY_TOKEN_SECRET is required in production")
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=_lifespan)
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
    app.include_router(dataset_image_sources_router, dependencies=[Depends(require_session)])
    app.include_router(agent_assets_router, dependencies=[Depends(require_session)])
    app.include_router(results_router, dependencies=[Depends(require_session)])
    app.include_router(traces_router, dependencies=[Depends(require_session)])
    app.include_router(eval_workbench_router, dependencies=[Depends(require_session)])
    app.include_router(runtimes_router, dependencies=[Depends(require_session)])
    app.include_router(model_configs_router, dependencies=[Depends(require_session)])
    # Resource connections/pools: authenticated members may read, administrators
    # own the writes (enforced per route in the router, M4-03).
    app.include_router(resources_router, dependencies=[Depends(require_session)])
    # The operations status endpoint also requires the administrator role
    # (enforced in the router); the session dependency is kept for consistency
    # with the other console APIs.
    app.include_router(operations_router, dependencies=[Depends(require_session)])
    # Delivery pipeline: members can read, only administrators can request the
    # controlled rollback (enforced per route in the router, M5-18).
    app.include_router(deployments_router)
    # The model gateway authenticates its own short-lived runner tokens.
    app.include_router(model_gateway_router)
    return app


app = create_app()
