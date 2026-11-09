"""Console identity: password hashing, signed sessions and role context.

The MVP follows the design commitments for requirement 1:

* passwords are stored as a salted scrypt hash (never plaintext);
* session cookies carry an HMAC-signed opaque token, while the database stores
  only its SHA-256 hash, expiry and revocation state;
* every request resolves the tenant membership so the console can enforce the
  administrator / read-only capability split.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AuditLog, AuthSession, Membership, Tenant, User, Workspace, new_id

MIN_PASSWORD_LENGTH = 8
ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
ROLE_LABELS = {ROLE_ADMIN: "平台管理员", ROLE_VIEWER: "只读成员"}

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 128 * 1024 * 1024

_TOKEN_VERSION = "v1"
_LAST_SEEN_INTERVAL = timedelta(minutes=5)


class IdentityError(ValueError):
    """Raised for invalid identity input that can be corrected by the caller."""


@dataclass(frozen=True)
class AuthContext:
    user: User
    membership: Membership
    tenant: Tenant
    workspace: Workspace | None

    @property
    def role(self) -> str:
        return self.membership.role

    def as_payload(self) -> dict[str, object]:
        return {
            "user": {"id": self.user.id, "email": self.user.email},
            "tenant": {"slug": self.tenant.slug, "name": self.tenant.name},
            "workspace": (
                {"slug": self.workspace.slug, "name": self.workspace.name}
                if self.workspace is not None
                else None
            ),
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
        }


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone=True columns."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise IdentityError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符。")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # Used when the account does not exist so the failure path still performs a
    # comparable scrypt computation and does not leak account existence by timing.
    return hash_password("dummy-password-for-timing")


def hash_password(password: str) -> str:
    """Return a salted scrypt hash in a self-describing text format."""

    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        scheme, n_raw, r_raw, p_raw, salt_b64, hash_b64 = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


def _sign(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_session_token(secret: str, session_id: str, nonce: str) -> str:
    message = f"{_TOKEN_VERSION}.{session_id}.{nonce}"
    return f"{message}.{_sign(secret, message)}"


def parse_session_token(secret: str, token: str | None) -> tuple[str, str] | None:
    """Verify the HMAC signature and return (session_id, nonce)."""

    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != _TOKEN_VERSION:
        return None
    session_id, nonce, signature = parts[1], parts[2], parts[3]
    message = f"{_TOKEN_VERSION}.{session_id}.{nonce}"
    if not hmac.compare_digest(signature, _sign(secret, message)):
        return None
    return session_id, nonce


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def get_default_membership(
    session: Session, user_id: str, tenant_id: str | None = None
) -> Membership | None:
    statement = select(Membership).where(Membership.user_id == user_id)
    if tenant_id is not None:
        statement = statement.where(Membership.tenant_id == tenant_id)
    return session.scalar(
        statement.order_by(Membership.is_default.desc(), Membership.created_at.asc()).limit(1)
    )


def authenticate(session: Session, email: str, password: str) -> tuple[User, Membership] | None:
    """Return the active user and their default membership, else ``None``."""

    user = get_user_by_email(session, email)
    encoded = user.password_hash if user is not None else _dummy_hash()
    password_ok = verify_password(password, encoded)
    if user is None or not password_ok or not user.is_active:
        return None
    membership = get_default_membership(session, user.id)
    if membership is None:
        return None
    return user, membership


def build_context(session: Session, user: User, membership: Membership) -> AuthContext | None:
    tenant = session.get(Tenant, membership.tenant_id)
    if tenant is None or tenant.status != "active":
        return None
    workspace = session.get(Workspace, membership.workspace_id)
    return AuthContext(user=user, membership=membership, tenant=tenant, workspace=workspace)


def create_session(
    session: Session,
    *,
    user: User,
    membership: Membership,
    settings: Settings,
    remember: bool = False,
    now: datetime | None = None,
) -> tuple[AuthSession, str, datetime]:
    current = now or datetime.now(UTC)
    ttl = (
        timedelta(days=settings.auth_session_remember_days)
        if remember
        else timedelta(hours=settings.auth_session_ttl_hours)
    )
    expires_at = current + ttl
    session_id = new_id()
    nonce = secrets.token_urlsafe(24)
    token = issue_session_token(settings.session_secret, session_id, nonce)
    auth_session = AuthSession(
        id=session_id,
        tenant_id=membership.tenant_id,
        user_id=user.id,
        token_hash=session_token_hash(token),
        expires_at=expires_at,
        last_seen_at=current,
    )
    session.add(auth_session)
    return auth_session, token, expires_at


def resolve_session(
    session: Session,
    *,
    settings: Settings,
    token: str | None,
    now: datetime | None = None,
) -> AuthContext | None:
    parsed = parse_session_token(settings.session_secret, token)
    if parsed is None or token is None:
        return None
    session_id, _nonce = parsed
    current = now or datetime.now(UTC)
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == session_token_hash(token))
    )
    if auth_session is None or auth_session.id != session_id:
        return None
    expires_at = _as_utc(auth_session.expires_at)
    last_seen_at = _as_utc(auth_session.last_seen_at)
    if auth_session.revoked_at is not None or expires_at is None or expires_at <= current:
        return None
    user = session.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        return None
    membership = get_default_membership(session, user.id, auth_session.tenant_id)
    if membership is None:
        return None
    context = build_context(session, user, membership)
    if context is None:
        return None
    if (
        last_seen_at is None
        or current - last_seen_at >= _LAST_SEEN_INTERVAL
    ):
        auth_session.last_seen_at = current
        session.commit()
    return context


def revoke_session(session: Session, auth_session: AuthSession, now: datetime | None = None) -> None:
    if auth_session.revoked_at is None:
        auth_session.revoked_at = now or datetime.now(UTC)


def revoke_session_by_token(
    session: Session, *, settings: Settings, token: str | None, now: datetime | None = None
) -> AuthSession | None:
    if not token:
        return None
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == session_token_hash(token))
    )
    if auth_session is not None:
        revoke_session(session, auth_session, now=now)
    return auth_session


def revoke_user_sessions(session: Session, user_id: str, now: datetime | None = None) -> int:
    result = session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )
    return int(result.rowcount or 0)


def record_audit(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=metadata or {},
    )
    session.add(entry)
    return entry


def upsert_member(
    session: Session,
    *,
    email: str,
    password: str,
    tenant: Tenant,
    workspace: Workspace,
    role: str = ROLE_ADMIN,
    is_default: bool = True,
    reset_password: bool = False,
) -> User:
    """Create or update an account and its default tenant membership.

    The bootstrap path is idempotent: an existing account keeps its password
    unless ``reset_password`` is explicitly requested.
    """

    if role not in (ROLE_ADMIN, ROLE_VIEWER):
        raise IdentityError("角色只能是 admin 或 viewer。")
    validate_password(password)
    normalized = normalize_email(email)
    if not normalized:
        raise IdentityError("账号邮箱不能为空。")

    user = get_user_by_email(session, normalized)
    if user is None:
        user = User(email=normalized, password_hash=hash_password(password))
        session.add(user)
        session.flush()
    elif reset_password:
        user.password_hash = hash_password(password)
        revoke_user_sessions(session, user.id)

    membership = session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.user_id == user.id,
        )
    )
    if membership is None:
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role=role,
            is_default=is_default,
        )
        session.add(membership)
    else:
        membership.workspace_id = workspace.id
        membership.role = role
        membership.is_default = is_default
    session.flush()
    return user


def set_password(session: Session, *, email: str, password: str) -> User | None:
    validate_password(password)
    user = get_user_by_email(session, email)
    if user is None:
        return None
    user.password_hash = hash_password(password)
    revoke_user_sessions(session, user.id)
    session.flush()
    return user
