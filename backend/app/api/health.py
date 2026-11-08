from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(session: Session = Depends(get_session)) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - exercised by failure injection in CI
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "database_unavailable",
                    "message": "数据库暂不可用",
                    "request_id": None,
                }
            },
        ) from exc

    settings = get_settings()
    if settings.redis_url:
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # pragma: no cover - depends on external Redis
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "redis_unavailable",
                        "message": "Redis 暂不可用",
                        "request_id": None,
                    }
                },
            ) from exc
    else:
        checks["redis"] = "disabled"

    return {"status": "ok", "checks": checks}
