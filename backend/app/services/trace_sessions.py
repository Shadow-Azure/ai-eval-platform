"""Shared read helpers for the attempt's main (root) Agent session."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TraceSession


def main_session_id(
    session: Session, attempt_id: str, *, tenant_id: str | None = None
) -> str | None:
    """Native id of an attempt's root (main) Agent session, if parsed.

    This is the same semantic used by self-evolution reporting and by the
    evaluation case list: the first root session (``parent_session_id`` is
    null) that is not classified as ``unlinked``. Empty ids are treated as
    absent so callers can render a neutral empty state.
    """

    rows = main_session_ids(session, [attempt_id], tenant_id=tenant_id)
    return rows.get(attempt_id)


def main_session_ids(
    session: Session,
    attempt_ids: Iterable[str],
    *,
    tenant_id: str | None = None,
) -> dict[str, str]:
    """Batch variant of :func:`main_session_id` keyed by attempt id.

    One query per page keeps the case list free of N+1 lookups. When several
    root sessions exist, the lowest ``ordinal`` wins, matching the reporting
    contract.
    """

    ids = sorted({attempt_id for attempt_id in attempt_ids if attempt_id})
    if not ids:
        return {}
    statement = select(TraceSession).where(
        TraceSession.eval_case_attempt_id.in_(ids),
        TraceSession.parent_session_id.is_(None),
        TraceSession.status != "unlinked",
        TraceSession.native_session_id != "",
    )
    if tenant_id is not None:
        statement = statement.where(TraceSession.tenant_id == tenant_id)
    rows = session.scalars(
        statement.order_by(
            TraceSession.eval_case_attempt_id,
            TraceSession.ordinal,
            TraceSession.id,
        )
    )
    found: dict[str, str] = {}
    for row in rows:
        if row.eval_case_attempt_id not in found:
            found[row.eval_case_attempt_id] = row.native_session_id
    return found
