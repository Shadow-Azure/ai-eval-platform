"""Idempotent bootstrap of the default tenant/workspace/project."""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .defaults import (
    DEFAULT_PROJECT_ID,
    DEFAULT_PROJECT_NAME,
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_NAME,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
)
from .models import Project, Tenant, User, Workspace
from .services import identity


def ensure_default_scope(session: Session) -> tuple[Tenant, Workspace, Project]:
    """Create the single-tenant default scope if it does not exist yet."""

    settings = get_settings()

    tenant = session.scalar(
        select(Tenant).where(Tenant.slug == settings.default_tenant_slug)
    )
    if tenant is None:
        tenant = Tenant(
            id=str(DEFAULT_TENANT_ID),
            slug=settings.default_tenant_slug,
            name=DEFAULT_TENANT_NAME,
            status="active",
        )
        session.add(tenant)
        session.flush()

    workspace = session.scalar(
        select(Workspace).where(
            Workspace.tenant_id == tenant.id,
            Workspace.slug == settings.default_workspace_slug,
        )
    )
    if workspace is None:
        workspace = Workspace(
            id=str(DEFAULT_WORKSPACE_ID),
            tenant_id=tenant.id,
            slug=settings.default_workspace_slug,
            name=DEFAULT_WORKSPACE_NAME,
        )
        session.add(workspace)
        session.flush()

    project = session.scalar(
        select(Project).where(
            Project.tenant_id == tenant.id,
            Project.workspace_id == workspace.id,
            Project.slug == settings.default_project_slug,
        )
    )
    if project is None:
        project = Project(
            id=str(DEFAULT_PROJECT_ID),
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            slug=settings.default_project_slug,
            name=DEFAULT_PROJECT_NAME,
        )
        session.add(project)
        session.flush()

    session.commit()
    return tenant, workspace, project


def ensure_bootstrap_admin(
    session: Session,
    tenant: Tenant,
    workspace: Workspace,
    settings=None,
) -> User | None:
    """Create the first administrator when bootstrap credentials are configured.

    This is intentionally bootstrap-only: an existing account keeps its
    password. Use ``python -m app.manage set-password`` to reset it later.
    """

    settings = settings or get_settings()
    password = (settings.auth_bootstrap_admin_password or "").strip()
    if not password:
        return None
    user = identity.upsert_member(
        session,
        email=settings.auth_bootstrap_admin_email,
        password=password,
        tenant=tenant,
        workspace=workspace,
        role=identity.ROLE_ADMIN,
        is_default=True,
    )
    session.commit()
    return user


def main() -> int:
    with SessionLocal() as session:
        tenant, workspace, project = ensure_default_scope(session)
        admin = ensure_bootstrap_admin(session, tenant, workspace)
    print(
        f"default scope ready: tenant={tenant.slug} workspace={workspace.slug} "
        f"project={project.slug}",
        file=sys.stdout,
    )
    if admin is None:
        print(
            "auth: no bootstrap administrator configured; set "
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD (and optionally "
            "AUTH_BOOTSTRAP_ADMIN_EMAIL) or run "
            "`python -m app.manage set-password --email <email>`",
            file=sys.stderr,
        )
    else:
        print(f"auth: bootstrap administrator ready: {admin.email}", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
