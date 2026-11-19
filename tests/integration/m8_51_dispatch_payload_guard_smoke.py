#!/usr/bin/env python3
"""M8-51 (#323) dispatch payload guard smoke.

Run manually (not collected by pytest):

    .venv/bin/python tests/integration/m8_51_dispatch_payload_guard_smoke.py \
      --out /tmp/m8-51-evidence.json

The smoke builds a disposable SQLite control plane and drives the real outbox
dispatcher against a fake GitHub transport. It covers the acceptance timeline:

1. a complete scripts-bundle configuration still dispatches a 10-property
   client_payload, with the pin in the outbox row and not in the payload;
2. an injected 11th top-level property fails locally with
   ``dispatch_payload_invalid`` and never calls the transport;
3. a 422 parameter-validation response is terminal (no retry);
4. a 422 "spammed" response keeps the retry/backoff path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BUNDLE_NAME = "eval-runner-scripts-0123456789abcdef0123456789abcdef.tar.gz"


class FakeTransport:
    def __init__(self, responses: list[tuple[int, str]] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def __call__(self, url: str, *, method: str, body, headers, timeout: float):
        payload = json.loads(body.decode())
        self.calls.append({"url": url, "method": method, "body": payload})
        properties = len(payload.get("client_payload") or {})
        if properties > 10:
            return (
                422,
                '{"message":"No more than 10 properties are allowed; '
                f'{properties} were supplied."}}',
            )
        return self.responses.pop(0) if self.responses else (204, "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/m8-51-evidence.json")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="m8-51-smoke-"))
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m8-51-smoke-session-secret",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m8-51-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m8-51-smoke-password",
            "GITHUB_TOKEN": "m8-51-smoke-token",
            "GITHUB_REPO": "Shadow-Azure/ai-eval-platform",
            "RUNNER_SCRIPTS_BUNDLE_NAME": BUNDLE_NAME,
            "RUNNER_SCRIPTS_BUNDLE_MD5": "0" * 32,
            "RUNNER_SCRIPTS_BUNDLE_SHA256": "a" * 64,
            "RUNNER_SCRIPTS_BUNDLE_SIZE": "40960",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "app.seed"], cwd=BACKEND, env=env, check=True, capture_output=True
    )

    inherited = ("DATABASE_URL", "REDIS_", "AUTH_", "GITHUB_", "RUNNER_")
    os.environ.update({key: env[key] for key in env if key.startswith(inherited)})
    sys.path.insert(0, str(BACKEND))

    from app.config import get_settings  # noqa: PLC0415
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import EvalCaseAttempt, EvalCaseRun, EvalRun, Outbox  # noqa: PLC0415
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services import dispatcher, evaluations  # noqa: PLC0415

    settings = get_settings()
    session = SessionLocal()
    ensure_default_scope(session)
    evidence: dict[str, object] = {}

    def create_run(key: str):
        run, _ = evaluations.create_synthetic_evaluation(
            session,
            name=f"m8-51 smoke {key}",
            case_keys=["a", "b"],
            idempotency_key=f"m8-51-smoke-{key}",
            created_by="agent",
            settings=settings,
        )
        return run

    def row_for(run) -> Outbox:
        return (
            session.query(Outbox)
            .filter(Outbox.aggregate_id == run.id)
            .order_by(Outbox.id)
            .all()[-1]
        )

    # Scenario 1: complete bundle config → 10-property payload, pin in row only.
    run1 = create_run("bundle")
    transport1 = FakeTransport([(204, "")])
    outcome1 = dispatcher.dispatch_pending_outbox(session, transport=transport1)
    row1 = row_for(run1)
    payload1 = transport1.calls[0]["body"]["client_payload"]
    serialized = json.dumps(payload1, ensure_ascii=False).encode("utf-8")
    assert outcome1.dispatched == 1
    assert len(payload1) == 10, f"expected 10 properties, got {len(payload1)}"
    assert len(serialized) < 60 * 1024
    assert "scripts_bundle" not in payload1
    assert row1.payload_json["scripts_bundle"]["name"] == BUNDLE_NAME
    assert row1.status == "sent"
    evidence["scenario_1_ten_properties"] = {
        "client_payload_properties": len(payload1),
        "client_payload_bytes": len(serialized),
        "scripts_bundle_in_payload": "scripts_bundle" in payload1,
        "scripts_bundle_in_outbox_row": row1.payload_json["scripts_bundle"],
        "github_status": row1.payload_json["github_status"],
    }

    # Scenario 2: an injected 11th property fails locally, transport untouched.
    run2 = create_run("eleven")
    row2 = row_for(run2)
    payload2 = dict(row2.payload_json["client_payload"])
    payload2["extra"] = "boom"
    row2.payload_json = {**row2.payload_json, "client_payload": payload2}
    session.commit()
    transport2 = FakeTransport()
    outcome2 = dispatcher.dispatch_pending_outbox(session, transport=transport2)
    row2 = row_for(run2)
    attempts2 = (
        session.query(EvalCaseAttempt)
        .join(EvalCaseRun, EvalCaseRun.id == EvalCaseAttempt.eval_case_run_id)
        .filter(
            EvalCaseRun.eval_run_id == run2.id,
            EvalCaseAttempt.shard_id == row2.payload_json["shard_id"],
        )
        .all()
    )
    assert transport2.calls == []
    assert outcome2.failed == 1
    assert row2.status == "failed"
    assert "dispatch_payload_invalid" in row2.payload_json["last_error"]
    assert "11" in row2.payload_json["last_error"]
    assert {attempt.status for attempt in attempts2} == {"dispatch_failed"}
    evidence["scenario_2_eleven_properties"] = {
        "transport_calls": len(transport2.calls),
        "outbox_status": row2.status,
        "attempts_status": sorted({attempt.status for attempt in attempts2}),
        "last_error": row2.payload_json["last_error"],
    }

    # Scenario 3: 422 parameter validation is terminal.
    run3 = create_run("422-terminal")
    transport3 = FakeTransport(
        [(422, '{"message":"No more than 10 properties are allowed; 11 were supplied."}')]
    )
    outcome3 = dispatcher.dispatch_pending_outbox(session, transport=transport3)
    row3 = row_for(run3)
    assert outcome3.retried == 0
    assert row3.status == "failed"
    assert row3.attempts == 1
    assert "github_dispatch_failed:422" in row3.payload_json["last_error"]
    evidence["scenario_3_422_terminal"] = {
        "transport_calls": len(transport3.calls),
        "outbox_status": row3.status,
        "attempts": row3.attempts,
        "last_error": row3.payload_json["last_error"],
    }

    # Scenario 4: a spammed 422 keeps retrying.
    run4 = create_run("422-spam")
    transport4 = FakeTransport([(422, '{"message":"The endpoint has been spammed."}')])
    outcome4 = dispatcher.dispatch_pending_outbox(session, transport=transport4)
    row4 = row_for(run4)
    assert outcome4.retried == 1
    assert row4.status == "pending"
    assert row4.attempts == 1
    evidence["scenario_4_422_spam_retry"] = {
        "transport_calls": len(transport4.calls),
        "outbox_status": row4.status,
        "attempts": row4.attempts,
        "last_error": row4.payload_json["last_error"],
    }

    run_row = session.get(EvalRun, run2.id)
    evidence["terminal_run_status"] = run_row.status
    evidence["settings"] = {
        "bundle_name": settings.runner_scripts_bundle_name,
        "event_type": settings.github_dispatch_event_type,
    }

    Path(args.out).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
