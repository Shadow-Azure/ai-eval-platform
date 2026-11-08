from __future__ import annotations

from app.models import Project, Tenant, Workspace
from app.seed import ensure_default_scope
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def test_default_scope_is_created_once(engine) -> None:
    with Session(engine) as session:
        tenant, workspace, project = ensure_default_scope(session)
        first = (tenant.id, workspace.id, project.id)
        tenant_again, workspace_again, project_again = ensure_default_scope(session)
        second = (tenant_again.id, workspace_again.id, project_again.id)

        assert first == second
        assert session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert tenant.slug == "default"
        assert workspace.tenant_id == tenant.id
        assert project.workspace_id == workspace.id
