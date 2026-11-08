from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..models import EvalRun, Project, Runner, Tenant

router = APIRouter(tags=["overview"])


@router.get("/api/v1/overview")
def overview(session: Session = Depends(get_session)) -> dict[str, object]:
    settings = get_settings()
    tenant = session.scalar(
        select(Tenant).where(Tenant.slug == settings.default_tenant_slug)
    )
    if tenant is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "default_scope_missing",
                    "message": "默认租户尚未初始化，请先执行迁移与 seed",
                    "request_id": None,
                }
            },
        )
    project = session.scalar(
        select(Project).where(
            Project.tenant_id == tenant.id,
            Project.slug == settings.default_project_slug,
        )
    )

    def count_eval(status: str | None = None) -> int:
        statement = select(func.count()).select_from(EvalRun).where(
            EvalRun.tenant_id == tenant.id
        )
        if status is not None:
            statement = statement.where(EvalRun.status == status)
        return int(session.scalar(statement) or 0)

    runners_total = int(
        session.scalar(
            select(func.count()).select_from(Runner).where(Runner.tenant_id == tenant.id)
        )
        or 0
    )
    runners_online = int(
        session.scalar(
            select(func.count())
            .select_from(Runner)
            .where(
                Runner.tenant_id == tenant.id,
                Runner.status.in_(("online", "busy")),
            )
        )
        or 0
    )

    return {
        "status": "ok",
        "tenant": {"slug": tenant.slug, "name": tenant.name},
        "project": (
            {"slug": project.slug, "name": project.name}
            if project is not None
            else None
        ),
        "counts": {
            "eval_runs": count_eval(),
            "running": count_eval("running"),
            "queued": count_eval("queued"),
            "failed": count_eval("failed"),
            "runners_online": runners_online,
            "runners_total": runners_total,
        },
    }
