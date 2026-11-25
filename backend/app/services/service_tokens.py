"""M10 (#429): service tokens — machine identity for template launches.

Harness-evolution and other automation cannot hold a browser session, so the
stable execution APIs also accept a bearer service token. This module owns the
credential lifecycle, the scope/allowlist checks and the launch caps.

Security contract:

* the plaintext (``hev_`` + 32 random URL-safe bytes) is returned exactly once,
  by :func:`create_token`, and never persisted or logged;
* only the SHA-256 hex of the full plaintext is stored, so a leaked row cannot
  be replayed as a credential;
* revoked and expired tokens never authenticate, and the last successful use is
  recorded for audit;
* launch caps are enforced before any asset work or run creation, so an
  over-limit caller cannot consume capacity.

Errors carry ``status``/``code``/``field``/``details`` so the API layer can
reuse the console's shared ``{"error": {...}}`` envelope.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..defaults import DEFAULT_TENANT_ID
from ..models import SERVICE_TOKEN_SCOPES, EvalRun, ServiceToken
from ..money import MoneyError, money_str, money_value
from .evaluations import TERMINAL_RUN_STATUSES

# The plaintext prefix is part of the contract: the auth dependency rejects a
# bearer value that does not start with it, and support can tell a service
# token apart from a console session at a glance.
TOKEN_PREFIX = "hev_"
TOKEN_BYTES = 32

MAX_NAME_LENGTH = 100
MAX_ALLOWLIST_ITEMS = 100
MAX_TEMPLATE_ID_LENGTH = 36
MAX_CREATED_BY_LENGTH = 128


class ServiceTokenError(Exception):
    """Base error mapped to the console's ``{"error": {...}}`` envelope."""

    status = 422
    code = "service_token_invalid"

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        details: dict | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.details = details or {}
        if code:
            self.code = code


class ServiceTokenInvalid(ServiceTokenError):
    status = 422
    code = "service_token_invalid"


class ServiceTokenNotFound(ServiceTokenError):
    status = 404
    code = "service_token_not_found"


class ServiceTokenConflict(ServiceTokenError):
    status = 409
    code = "service_token_name_conflict"


class ServiceTokenLimit(ServiceTokenError):
    status = 429
    code = "service_token_limit_exceeded"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone=True columns."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    value = _as_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _is_active(token: ServiceToken, *, now: datetime | None = None) -> bool:
    if token.revoked_at is not None:
        return False
    expires_at = _as_utc(token.expires_at)
    return expires_at is None or expires_at > _now(now)


def _invalid(message: str, *, field: str) -> ServiceTokenInvalid:
    return ServiceTokenInvalid(message, field=field)


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("服务令牌名称必须是字符串", field="name")
    name = value.strip()
    if not name:
        raise _invalid("服务令牌名称不能为空", field="name")
    if len(name) > MAX_NAME_LENGTH:
        raise _invalid(f"服务令牌名称不能超过 {MAX_NAME_LENGTH} 个字符", field="name")
    return name


def _scopes(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise _invalid("服务令牌 scope 必须是数组", field="scopes")
    scopes: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in SERVICE_TOKEN_SCOPES:
            allowed = "、".join(SERVICE_TOKEN_SCOPES)
            raise _invalid(f"服务令牌 scope 只能是 {allowed}", field="scopes")
        if item not in scopes:
            scopes.append(item)
    if not scopes:
        raise _invalid("服务令牌至少需要一个 scope", field="scopes")
    return scopes


def _template_allowlist(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise _invalid("模板白名单必须是数组", field="template_allowlist")
    if len(value) > MAX_ALLOWLIST_ITEMS:
        raise _invalid(
            f"模板白名单不能超过 {MAX_ALLOWLIST_ITEMS} 个模板",
            field="template_allowlist",
        )
    allowlist: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _invalid("模板白名单只能包含模板 ID 字符串", field="template_allowlist")
        template_id = item.strip()
        if not template_id:
            raise _invalid("模板白名单不能包含空模板 ID", field="template_allowlist")
        if len(template_id) > MAX_TEMPLATE_ID_LENGTH:
            raise _invalid(
                f"模板 ID 不能超过 {MAX_TEMPLATE_ID_LENGTH} 个字符",
                field="template_allowlist",
            )
        if template_id not in allowlist:
            allowlist.append(template_id)
    return allowlist


def _max_concurrent_runs(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid("并发上限必须是整数", field="max_concurrent_runs")
    if value < 1:
        raise _invalid("并发上限必须大于等于 1", field="max_concurrent_runs")
    return value


def _daily_budget(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = money_value(value)
    except (MoneyError, InvalidOperation, TypeError, ValueError) as error:
        raise _invalid("当日预算必须是数字", field="daily_budget_cny") from error
    if not amount.is_finite():
        raise _invalid("当日预算必须是有限数字", field="daily_budget_cny")
    if amount < 0:
        raise _invalid("当日预算必须大于等于 0", field="daily_budget_cny")
    return amount


def _expires_at(value: Any, *, now: datetime) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _invalid("过期时间必须是带时区的日期时间", field="expires_at")
    expires_at = value.astimezone(UTC)
    if expires_at <= now:
        raise _invalid("过期时间必须晚于当前时间", field="expires_at")
    return expires_at


def create_token(
    session: Session,
    data: dict,
    *,
    created_by: str,
    now: datetime | None = None,
) -> tuple[ServiceToken, str]:
    """Create a token and return ``(row, plaintext)``.

    The plaintext is the only copy that will ever exist outside the caller's
    hands: it is not stored, not logged and not derivable from ``token_hash``.
    """

    if not isinstance(data, dict):
        raise ServiceTokenInvalid("请求体必须是对象")
    current = _now(now)
    name = _name(data.get("name"))
    scopes = _scopes(data.get("scopes"))
    allowlist = _template_allowlist(data.get("template_allowlist"))
    max_concurrent_runs = _max_concurrent_runs(data.get("max_concurrent_runs"))
    daily_budget_cny = _daily_budget(data.get("daily_budget_cny"))
    expires_at = _expires_at(data.get("expires_at"), now=current)

    existing = session.scalar(
        select(ServiceToken.id).where(
            ServiceToken.tenant_id == str(DEFAULT_TENANT_ID),
            ServiceToken.name == name,
        )
    )
    if existing is not None:
        raise ServiceTokenConflict("服务令牌名称已存在", field="name")

    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    token = ServiceToken(
        tenant_id=str(DEFAULT_TENANT_ID),
        name=name,
        token_hash=_hash_token(plaintext),
        scopes_json=scopes,
        template_allowlist_json=allowlist,
        max_concurrent_runs=max_concurrent_runs,
        daily_budget_cny=daily_budget_cny,
        expires_at=expires_at,
        created_by=str(created_by or "")[:MAX_CREATED_BY_LENGTH],
    )
    session.add(token)
    try:
        session.commit()
    except IntegrityError as error:
        # A concurrent create can win the unique-name race after the pre-check.
        session.rollback()
        raise ServiceTokenConflict("服务令牌名称已存在", field="name") from error
    return token, plaintext


def list_tokens(session: Session) -> list[ServiceToken]:
    """Tenant tokens, newest first. Never includes secret material."""

    return list(
        session.scalars(
            select(ServiceToken)
            .where(ServiceToken.tenant_id == str(DEFAULT_TENANT_ID))
            .order_by(ServiceToken.created_at.desc(), ServiceToken.id.desc())
        )
    )


def _get_token(session: Session, token_id: str) -> ServiceToken:
    token = session.scalar(
        select(ServiceToken).where(
            ServiceToken.id == token_id,
            ServiceToken.tenant_id == str(DEFAULT_TENANT_ID),
        )
    )
    if token is None:
        raise ServiceTokenNotFound("服务令牌不存在")
    return token


def revoke_token(
    session: Session, token_id: str, *, now: datetime | None = None
) -> ServiceToken:
    """Revoke a token. Revoking an already revoked token is idempotent and keeps
    the first timestamp, so the audit trail does not move."""

    token = _get_token(session, token_id)
    if token.revoked_at is None:
        token.revoked_at = _now(now)
        session.commit()
    return token


def authenticate(
    session: Session, raw_token: str | None, *, now: datetime | None = None
) -> ServiceToken | None:
    """Resolve a bearer plaintext to its active row, recording the last use.

    Missing, malformed, unknown, revoked and expired tokens all return ``None``
    so the dependency can answer one generic 401 without leaking which check
    failed.
    """

    if not raw_token or not isinstance(raw_token, str):
        return None
    if not raw_token.startswith(TOKEN_PREFIX):
        return None
    token = session.scalar(
        select(ServiceToken).where(
            ServiceToken.token_hash == _hash_token(raw_token),
            ServiceToken.tenant_id == str(DEFAULT_TENANT_ID),
        )
    )
    if token is None:
        return None
    current = _now(now)
    if not _is_active(token, now=current):
        return None
    token.last_used_at = current
    session.commit()
    return token


def enforce_launch_caps(
    session: Session, token: ServiceToken, *, now: datetime | None = None
) -> None:
    """Reject a launch that would exceed the token's concurrency/budget caps.

    Both caps count the runs the token itself created (``created_by`` is the
    ``svc:<name>`` actor label), so a console user with the same template is
    never charged to the machine identity.
    """

    current = _now(now)
    actor = actor_label(token)
    tenant_id = str(DEFAULT_TENANT_ID)

    if token.max_concurrent_runs is not None:
        active = int(
            session.scalar(
                select(func.count())
                .select_from(EvalRun)
                .where(
                    EvalRun.tenant_id == tenant_id,
                    EvalRun.created_by == actor,
                    EvalRun.status.not_in(TERMINAL_RUN_STATUSES),
                )
            )
            or 0
        )
        cap = int(token.max_concurrent_runs)
        if active >= cap:
            raise ServiceTokenLimit(
                f"服务令牌的并发评测数已达上限（{active}/{cap}）",
                details={
                    "limit": "max_concurrent_runs",
                    "cap": cap,
                    "active": active,
                },
            )

    if token.daily_budget_cny is not None:
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = session.scalar(
            select(func.coalesce(func.sum(EvalRun.budget_spent_cny), 0)).where(
                EvalRun.tenant_id == tenant_id,
                EvalRun.created_by == actor,
                EvalRun.created_at >= day_start,
            )
        )
        spent_amount = money_value(spent) if spent is not None else Decimal("0")
        cap_amount = money_value(token.daily_budget_cny)
        if spent_amount >= cap_amount:
            raise ServiceTokenLimit(
                "服务令牌的当日预算已用尽",
                details={
                    "limit": "daily_budget_cny",
                    "cap": money_str(cap_amount),
                    "spent": money_str(spent_amount),
                },
            )


def token_view(token: ServiceToken) -> dict:
    """Admin-facing view: metadata only, never the hash or the plaintext."""

    return {
        "id": token.id,
        "name": token.name,
        "scopes": list(token.scopes_json or []),
        "template_allowlist": list(token.template_allowlist_json or []),
        "max_concurrent_runs": token.max_concurrent_runs,
        "daily_budget_cny": money_str(token.daily_budget_cny),
        "expires_at": _iso(token.expires_at),
        "revoked_at": _iso(token.revoked_at),
        "last_used_at": _iso(token.last_used_at),
        "created_by": token.created_by,
        "created_at": _iso(token.created_at),
        "active": _is_active(token),
    }


def actor_label(token: ServiceToken) -> str:
    """The ``created_by``/audit label a machine-triggered run records."""

    return f"svc:{token.name}"
