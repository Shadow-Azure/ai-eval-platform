from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.datasets import router as datasets_router
from .api.health import router as health_router
from .api.model_configs import router as model_configs_router
from .api.model_gateway import router as model_gateway_router
from .api.overview import router as overview_router
from .api.results import router as results_router
from .config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(overview_router)
    app.include_router(datasets_router)
    app.include_router(model_configs_router)
    app.include_router(model_gateway_router)
    app.include_router(results_router)
    return app


app = create_app()
