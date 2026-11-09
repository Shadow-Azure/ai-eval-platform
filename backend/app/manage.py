"""Administrative identity CLI.

Examples::

    python -m app.manage list-users
    python -m app.manage set-password --email admin@acme.local --password-stdin
    python -m app.manage create-admin --email admin@acme.local --password-stdin

Passwords are read from stdin or an interactive prompt; they are never taken
from a command-line argument so they do not land in shell history or process
listings.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Credential, Membership, ModelConfig, User, utcnow
from .secrets import SecretBoxError, get_secret_box, mask_secret
from .seed import ensure_default_scope
from .services import identity


def read_password(password_stdin: bool) -> str:
    if password_stdin:
        value = sys.stdin.readline()
        if not value:
            raise identity.IdentityError("未能从标准输入读取密码。")
        return value.rstrip("\r\n")
    value = getpass.getpass("新密码: ")
    confirm = getpass.getpass("再次输入新密码: ")
    if value != confirm:
        raise identity.IdentityError("两次输入的密码不一致。")
    return value


def cmd_list_users(_args: argparse.Namespace) -> int:
    with SessionLocal() as session:
        users = session.scalars(select(User).order_by(User.email)).all()
        if not users:
            print("no users")
            return 0
        for user in users:
            memberships = session.scalars(
                select(Membership).where(Membership.user_id == user.id)
            ).all()
            scopes = ", ".join(
                f"{membership.tenant_id}/{membership.role}"
                f"{' (default)' if membership.is_default else ''}"
                for membership in memberships
            )
            state = "active" if user.is_active else "disabled"
            print(f"{user.email}\t{state}\t{scopes}")
    return 0


def _set_password(session: Session, email: str, password: str) -> User | None:
    user = identity.set_password(session, email=email, password=password)
    if user is None:
        return None
    tenant_id = session.scalar(
        select(Membership.tenant_id).where(Membership.user_id == user.id)
    )
    if tenant_id:
        identity.record_audit(
            session,
            tenant_id=tenant_id,
            actor_user_id=None,
            action="auth.password_reset",
            target_type="user",
            target_id=user.id,
        )
    session.commit()
    return user


def cmd_set_password(args: argparse.Namespace) -> int:
    password = read_password(args.password_stdin)
    with SessionLocal() as session:
        user = _set_password(session, args.email, password)
    if user is None:
        print(f"user not found: {args.email}", file=sys.stderr)
        return 1
    print(f"password updated and active sessions revoked: {user.email}")
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    password = read_password(args.password_stdin)
    with SessionLocal() as session:
        tenant, workspace, _project = ensure_default_scope(session)
        user = identity.upsert_member(
            session,
            email=args.email,
            password=password,
            tenant=tenant,
            workspace=workspace,
            role=identity.ROLE_ADMIN,
            is_default=True,
        )
        identity.record_audit(
            session,
            tenant_id=tenant.id,
            actor_user_id=user.id,
            action="auth.admin_upsert",
            target_type="user",
            target_id=user.id,
        )
        session.commit()
    print(f"administrator ready: {user.email}")
    return 0




def cmd_upsert_model_config(args: argparse.Namespace) -> int:
    """Create or rotate the provider credential used by M3 evaluations.

    The API key is read from stdin so it never lands in shell history. The row
    is encrypted with SECRET_BOX_KEY (required in production).
    """

    api_key = sys.stdin.readline().rstrip("\r\n")
    if not api_key:
        print("error: 未能从标准输入读取模型 API Key", file=sys.stderr)
        return 2
    try:
        ciphertext, key_version = get_secret_box().encrypt(api_key)
    except SecretBoxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    pricing = {}
    if args.input_per_1m is not None:
        pricing["input_per_1m"] = args.input_per_1m
    if args.output_per_1m is not None:
        pricing["output_per_1m"] = args.output_per_1m
    with SessionLocal() as session:
        tenant, _workspace, project = ensure_default_scope(session)
        config = session.scalar(
            select(ModelConfig).where(
                ModelConfig.tenant_id == tenant.id,
                ModelConfig.project_id == project.id,
                ModelConfig.provider == args.provider,
                ModelConfig.base_url == args.base_url.rstrip("/"),
                ModelConfig.model_name == args.model_name,
            )
        )
        if config is None:
            credential = Credential(
                tenant_id=tenant.id,
                project_id=project.id,
                kind="model",
                provider=args.provider,
                ciphertext=ciphertext,
                key_version=key_version,
                secret_mask=mask_secret(api_key),
                last_rotated_at=utcnow(),
            )
            session.add(credential)
            session.flush()
            config = ModelConfig(
                tenant_id=tenant.id,
                project_id=project.id,
                credential_id=credential.id,
                provider=args.provider,
                display_name=args.display_name or args.model_name,
                base_url=args.base_url.rstrip("/"),
                model_name=args.model_name,
                api_style=args.api_style,
                limits_json={},
                pricing_json=pricing,
            )
            session.add(config)
        else:
            if pricing:
                config.pricing_json = pricing
            credential = session.get(Credential, config.credential_id)
            if credential is None:
                credential = Credential(
                    tenant_id=tenant.id,
                    project_id=project.id,
                    kind="model",
                    provider=args.provider,
                    ciphertext=ciphertext,
                    key_version=key_version,
                    secret_mask=mask_secret(api_key),
                    last_rotated_at=utcnow(),
                )
                session.add(credential)
                session.flush()
                config.credential_id = credential.id
            else:
                credential.ciphertext = ciphertext
                credential.key_version = key_version
                credential.secret_mask = mask_secret(api_key)
                credential.last_rotated_at = utcnow()
                credential.revoked_at = None
        session.commit()
        print(
            f"model config ready: {config.id} provider={config.provider} "
            f"model={config.model_name} mask={credential.secret_mask}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-users", help="list console accounts")
    list_parser.set_defaults(func=cmd_list_users)

    for name, help_text, func in (
        ("set-password", "reset an existing account password", cmd_set_password),
        ("create-admin", "create or promote the default administrator", cmd_create_admin),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--email", required=True)
        command.add_argument("--password-stdin", action="store_true")
        command.set_defaults(func=func)

    model_parser = subparsers.add_parser(
        "upsert-model-config",
        help="create or rotate a model provider credential (API key on stdin)",
    )
    model_parser.add_argument("--provider", required=True)
    model_parser.add_argument("--base-url", required=True)
    model_parser.add_argument("--model-name", required=True)
    model_parser.add_argument("--display-name", default=None)
    model_parser.add_argument("--api-style", choices=("openai", "anthropic"), default="openai")
    model_parser.add_argument("--input-per-1m", type=float, default=None)
    model_parser.add_argument("--output-per-1m", type=float, default=None)
    model_parser.set_defaults(func=cmd_upsert_model_config)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except identity.IdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
