"""Runner plane API (M2-04).

Routes consumed by the deployed fixed-runner heartbeat agent:

- POST /runner/v1/runners/heartbeat          — agent liveness + busy state
- GET  /api/v1/runners, /api/v1/runners/{id} — console queries (status/labels/heartbeat/task)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..model_gateway.tokens import GatewayClaims, issue_gateway_token
from ..models import Dataset, DatasetVersion, EvalCaseAttempt, EvalCaseRun, EvalRun, ModelConfig, Outbox
from ..runner_contracts import ContractInvalid, validate_payload
from ..services import agent_assets as asset_service
from ..services import case_attempts
from ..services import runners as runner_service
from ..services.case_attempts import CaseCallbackError
from ..services.evaluations import SHARD_DISPATCH_TOPIC
from ..services.object_store import object_store_from_settings
from ..services.short_token import ShortTokenInvalid, issue_short_token, verify_short_token
from .dependencies import require_session
from .errors import error_detail


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)

router = APIRouter(tags=["runners"])


class HeartbeatRequest(BaseModel):
    runner_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=64)
    status: Literal["online", "busy", "offline"] = "online"
    current_case_attempt_id: str | None = Field(default=None, max_length=128)
    # M4-04 spot runners join the pool that created them; fixed runners omit
    # this field and keep joining the fixed pool.
    resource_pool_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def _validate_attempt_binding(self) -> HeartbeatRequest:
        if self.status == "busy" and not self.current_case_attempt_id:
            raise ValueError("busy heartbeat 需要 current_case_attempt_id")
        if self.status != "busy" and self.current_case_attempt_id:
            raise ValueError("仅 busy 心跳可携带 current_case_attempt_id")
        return self


def require_runner_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer RUNNER_HEARTBEAT_TOKEN check; open when no token is configured."""

    expected = settings.runner_heartbeat_token
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=401,
            detail=error_detail("runner_token_invalid", "Runner 令牌无效"),
        )


@router.post("/runner/v1/runners/heartbeat", dependencies=[Depends(require_runner_token)])
def runner_heartbeat(
    payload: HeartbeatRequest,
    session: Session = Depends(get_session),
) -> dict:
    try:
        runner = runner_service.record_runner_heartbeat(
            session,
            runner_id=payload.runner_id,
            name=payload.name,
            labels=payload.labels,
            status=payload.status,
            current_case_attempt_id=payload.current_case_attempt_id,
            resource_pool_id=payload.resource_pool_id,
        )
    except runner_service.RunnerPoolNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail(
                "resource_pool_not_found",
                "Runner 心跳引用的资源池不存在或未启用",
                details={"resource_pool_id": str(exc)},
            ),
        ) from exc
    except runner_service.RunnerBusyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "runner_busy_conflict",
                "同一 Runner 同时只能执行一个 Job",
                details={
                    "bound_case_attempt_id": exc.runner.busy_case_attempt_id,
                    "incoming_case_attempt_id": exc.incoming_attempt,
                },
            ),
        ) from exc
    return {
        "ok": True,
        "status": runner.status,
        "runner_id": runner.provider_runner_id,
        "last_heartbeat_at": runner_service.runner_payload(runner)["last_heartbeat_at"],
    }


@router.get("/api/v1/runners", dependencies=[Depends(require_session)])
def list_runners(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    runner_service.sweep_stale_runners(
        session, timeout_seconds=settings.runner_heartbeat_timeout_seconds
    )
    runners = [
        runner_service.runner_payload(runner)
        for runner in runner_service.list_runners(session)
    ]
    return {"status": "ok", "runners": runners}


@router.get("/api/v1/runners/{provider_runner_id}", dependencies=[Depends(require_session)])
def get_runner(
    provider_runner_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    runner_service.sweep_stale_runners(
        session, timeout_seconds=settings.runner_heartbeat_timeout_seconds
    )
    try:
        runner = runner_service.get_runner_by_provider_id(session, provider_runner_id)
    except runner_service.RunnerNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=error_detail("runner_not_found", "Runner 不存在"),
        ) from exc
    return runner_service.runner_payload(runner)


# --------------------------------------------------------------------------
# Case-contract callbacks (runner-contract/schemas/v1) + shard manifest.
#
# M2-05: callbacks persist the attempt/case/run state machine
# (services/case_attempts.py) after validating the payload against the v1
# schemas, the attempt number and the platform-issued short-term token
# (design 06 §4; M2-04 leftover b). The manifest endpoint serves the exact
# byte-stable manifest text recorded in the dispatch outbox and issues the
# attempt-bound token via X-Runner-Task-Token.
# --------------------------------------------------------------------------

CASE_ACTION_KINDS: dict[str, str] = {
    "started": "started",
    "heartbeat": "heartbeat",
    "uploads": "upload",
    "completed": "completion",
    "failed": "failure",
}

# Callbacks that prove the runner is executing the case (bind), and those
# that end it (release) — same single-job rule as M2-04.
CASE_BIND_ACTIONS = ("started", "heartbeat", "uploads")
CASE_RELEASE_ACTIONS = ("completed", "failed")

DEV_TASK_SECRET = "development-only-secret"


def effective_task_secret(settings: Settings) -> str | None:
    """Secret for platform-issued short-term tokens.

    None → case endpoints are open (development only). The dev fallback keeps
    local/CI loops working without configuration and is safe only because no
    real model keys or cloud credentials flow through the synthetic loop.
    """

    return settings.runner_task_secret or DEV_TASK_SECRET


def require_case_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict | None:
    """Bearer <platform-issued short-term token> for case callbacks.

    Unset RUNNER_TASK_SECRET keeps the endpoint open for development; when
    configured, signature + expiry are checked here and the attempt/shard
    bindings are checked against the DB row in the handler.
    """

    if not settings.runner_task_secret:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=error_detail("runner_token_invalid", "缺少平台签发的短期 token"),
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = verify_short_token(token, effective_task_secret(settings))
    except ShortTokenInvalid as exc:
        raise HTTPException(
            status_code=401,
            detail=error_detail("runner_token_invalid", f"短期 token 校验失败：{exc.reason}"),
        ) from exc
    return claims


def _check_token_bindings(claims: dict | None, *, case_run, attempt) -> None:
    if claims is None:
        return
    mismatches = []
    if claims.get("eval_run_id") != case_run.eval_run_id:
        mismatches.append("eval_run_id")
    if claims.get("shard_id") != attempt.shard_id:
        mismatches.append("shard_id")
    if claims.get("attempt") != attempt.attempt_no:
        mismatches.append("attempt")
    if mismatches:
        raise HTTPException(
            status_code=403,
            detail=error_detail(
                "token_binding_mismatch",
                "token 与当前记录不一致",
                details={"mismatched": mismatches},
            ),
        )


def _runner_conflict(exc: runner_service.RunnerBusyConflict) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=error_detail(
            "runner_busy_conflict",
            "同一 Runner 同时只能执行一个 Job",
            details={
                "bound_case_attempt_id": exc.runner.busy_case_attempt_id,
                "incoming_case_attempt_id": exc.incoming_attempt,
            },
        ),
    )


def _callback_error(exc: CaseCallbackError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail=error_detail(exc.code, str(exc)),
    )


class UploadObjectRequest(BaseModel):
    artifact_kind: Literal[
        "score", "trace", "trace_metadata", "trace_diagnostic", "artifact"
    ]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    content_type: str = Field(min_length=1, max_length=128)


class UploadUrlsRequest(BaseModel):
    attempt: int = Field(ge=1)
    objects: list[UploadObjectRequest] = Field(min_length=1, max_length=128)


@router.post("/runner/v1/case-runs/{case_run_id}/upload-urls")
def case_upload_urls(
    case_run_id: str,
    payload: UploadUrlsRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    token_claims: dict | None = Depends(require_case_token),
) -> dict:
    """Exchange computed object digests for platform-owned keys and time-
    limited presigned PUT addresses (design 04 §5). Existence/size still gets
    verified at the uploads callback and at completion."""

    try:
        ctx = case_attempts.resolve_context(
            session, case_run_id=case_run_id, attempt_no=payload.attempt
        )
    except case_attempts.CaseNotFound as exc:
        raise _callback_error(exc) from exc
    except case_attempts.StaleAttempt as exc:
        raise _callback_error(exc) from exc
    _check_token_bindings(token_claims, case_run=ctx.case_run, attempt=ctx.attempt)

    store = object_store_from_settings(settings)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail=error_detail("object_store_unavailable", "对象存储未配置（S3_* 环境变量缺失）"),
        )

    now = datetime_now_utc()
    uploads = []
    for item in payload.objects:
        object_key = (
            f"evaluations/{ctx.run.id}/{ctx.attempt.shard_id}"
            f"/attempt{payload.attempt}/{case_run_id}/{item.artifact_kind}/{item.sha256}"
        )
        uploads.append(
            {
                "artifact_kind": item.artifact_kind,
                "sha256": item.sha256,
                "size": item.size,
                "object_key": object_key,
                "upload_url": store.presign_put_url(object_key, expires=900, now=now),
                "expires_at": (now + timedelta(seconds=900)).isoformat(timespec="microseconds"),
                "method": "PUT",
                "headers": {"content-type": item.content_type},
            }
        )
    return {"ok": True, "case_run_id": case_run_id, "uploads": uploads}


# M2-04 遗留 b 收口：case 回调只认平台签发的短期 token（require_case_token）；
# runner 面共享 token 只守卫 /runner/v1/runners/heartbeat。
@router.post("/runner/v1/case-runs/{case_run_id}/{action}")
def case_callback(
    case_run_id: str,
    action: Literal["started", "heartbeat", "uploads", "completed", "failed"],
    payload: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    token_claims: dict | None = Depends(require_case_token),
    x_runner_id: Annotated[str | None, Header()] = None,
) -> dict:
    kind = CASE_ACTION_KINDS[action]
    try:
        validate_payload(kind, payload)
    except ContractInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=error_detail("contract_invalid", f"{kind} 校验失败：{'; '.join(exc.errors)}"),
        ) from exc

    if payload.get("case_run_id") != case_run_id:
        raise HTTPException(
            status_code=422,
            detail=error_detail(
                "contract_invalid",
                f"payload.case_run_id ({payload.get('case_run_id')}) 与路径 case_run_id ({case_run_id}) 不一致",
            ),
        )

    # Resolve the attempt BEFORE touching runner state so unknown/old attempts
    # are rejected (409 stale_attempt) without side effects.
    try:
        ctx = case_attempts.resolve_context(
            session, case_run_id=case_run_id, attempt_no=payload["attempt"]
        )
    except case_attempts.CaseNotFound as exc:
        raise _callback_error(exc) from exc
    except case_attempts.StaleAttempt as exc:
        raise _callback_error(exc) from exc
    _check_token_bindings(token_claims, case_run=ctx.case_run, attempt=ctx.attempt)

    runner = None
    if x_runner_id:
        try:
            runner = runner_service.get_runner_by_provider_id(session, x_runner_id)
        except runner_service.RunnerNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail=error_detail("runner_not_found", "Runner 不存在"),
            ) from exc
        try:
            if action in CASE_BIND_ACTIONS:
                runner_service.bind_case(session, runner=runner, case_run_id=case_run_id)
            else:
                runner_service.release_case(session, runner=runner)
        except runner_service.RunnerBusyConflict as exc:
            raise _runner_conflict(exc) from exc

    handler = {
        "started": case_attempts.handle_started,
        "heartbeat": case_attempts.handle_heartbeat,
        "uploads": case_attempts.handle_uploads,
        "completed": case_attempts.handle_completed,
        "failed": case_attempts.handle_failed,
    }[action]
    kwargs: dict[str, Any] = {"payload": payload}
    if action in ("failed", "completed"):
        kwargs["settings"] = settings
    try:
        result = handler(session, **kwargs)
    except case_attempts.CaseCallbackError as exc:
        # A rejected callback (e.g. 429 capacity_waiting on started) must not
        # leave the runner bound to a case it never started, or the next job on
        # that runner would fail with runner_busy_conflict and skip the gate.
        if (
            runner is not None
            and action in CASE_BIND_ACTIONS
            and runner.busy_case_attempt_id == case_run_id
        ):
            runner_service.release_case(session, runner=runner)
        raise _callback_error(exc) from exc

    return {
        "ok": True,
        "case_run_id": case_run_id,
        "runner_id": runner.provider_runner_id if runner is not None else None,
        "runner_status": runner.status if runner is not None else None,
        "run_id": ctx.run.id,
        **result,
    }


@router.get("/runner/v1/shards/{shard_id}/manifest")
def shard_manifest(
    shard_id: str,
    eval_run_id: Annotated[str, Query()],
    attempt: Annotated[int, Query(ge=1)],
    dispatch_key: Annotated[str, Query()],
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve the byte-stable manifest recorded in the dispatch outbox and
    issue the attempt-bound short-term token (X-Runner-Task-Token)."""

    row = session.scalar(
        select(Outbox).where(
            Outbox.topic == SHARD_DISPATCH_TOPIC,
            Outbox.dispatch_key == dispatch_key,
        )
    )
    expected_key = f"{eval_run_id}:{shard_id}:{attempt}"
    if (
        row is None
        or row.dispatch_key != expected_key
        or row.payload_json.get("shard_id") != shard_id
        or int(row.payload_json.get("attempt", -1)) != attempt
        or row.payload_json.get("client_payload", {}).get("eval_run_id") != eval_run_id
    ):
        raise HTTPException(
            status_code=404,
            detail=error_detail("shard_dispatch_not_found", "分片派发记录不存在"),
        )

    run = session.get(EvalRun, row.aggregate_id)
    attempts = list(
        session.scalars(
            select(EvalCaseAttempt).where(
                EvalCaseAttempt.eval_case_run_id.in_(
                    select(EvalCaseRun.id).where(EvalCaseRun.eval_run_id == eval_run_id)
                ),
                EvalCaseAttempt.shard_id == shard_id,
                EvalCaseAttempt.attempt_no == attempt,
            )
        )
    )
    not_dispatchable = (
        run is None
        or run.status in ("cancelling", "cancelled", "pausing_budget", "paused_budget")
        or (attempts and all(a.status in case_attempts.ACTIVE_ATTEMPT_STATUSES is False for a in attempts))
    )
    if not_dispatchable:
        raise HTTPException(
            status_code=409,
            detail=error_detail(
                "shard_not_dispatchable",
                f"分片不可执行（run={run.status if run else 'missing'}）",
            ),
        )

    claims = {
        "eval_run_id": eval_run_id,
        "shard_id": shard_id,
        "attempt": attempt,
        "pool_id": run.resource_pool_id or "default",
    }
    token = issue_short_token(claims, effective_task_secret(settings), ttl_seconds=300)
    model_headers: dict[str, str] = {}
    if run.model_config_id:
        config = session.get(ModelConfig, run.model_config_id)
        if (
            config is None
            or config.tenant_id != run.tenant_id
            or not run.model_name
        ):
            raise HTTPException(
                status_code=409,
                detail=error_detail(
                    "model_config_unavailable",
                    "评测绑定的模型配置不可用，请联系管理员重新选择模型",
                ),
            )
        model_claims = GatewayClaims(
            tenant_id=run.tenant_id,
            model_config_id=config.id,
            allowed_models=(run.model_name,),
            eval_run_id=run.id,
            shard_id=shard_id,
            attempt=attempt,
            budget_cny=float(run.model_budget_cny) if run.model_budget_cny is not None else None,
        )
        model_token = issue_gateway_token(
            model_claims,
            settings.gateway_secret,
            ttl_seconds=settings.gateway_token_ttl_seconds,
        )
        gateway_base = settings.runner_callback_base_url.rstrip("/")
        model_headers = {
            "X-Runner-Model-Token": model_token,
            "X-Runner-Model-Name": run.model_name,
            "X-Runner-Model-Openai-Base-Url": f"{gateway_base}/model-gateway/v1",
            "X-Runner-Model-Anthropic-Base-Url": f"{gateway_base}/model-gateway/anthropic",
        }
    manifest_text = row.payload_json["manifest_text"]
    tasks_query = urlencode(
        {"eval_run_id": eval_run_id, "attempt": attempt, "dispatch_key": dispatch_key}
    )
    tasks_url = (
        f"{settings.runner_callback_base_url.rstrip('/')}"
        f"/runner/v1/shards/{shard_id}/tasks?{tasks_query}"
    )
    return Response(
        content=manifest_text,
        media_type="application/json",
        headers={
            "X-Runner-Task-Token": token,
            "X-Runner-Tasks-Url": tasks_url,
            "Cache-Control": "no-store",
            "X-Shard-Dispatch-Key": dispatch_key,
            **model_headers,
        },
    )


@router.get("/runner/v1/shards/{shard_id}/tasks")
def shard_tasks(
    shard_id: str,
    eval_run_id: Annotated[str, Query()],
    attempt: Annotated[int, Query(ge=1)],
    dispatch_key: Annotated[str, Query()],
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    token_claims: dict | None = Depends(require_case_token),
) -> dict:
    """Frozen per-case task view for the runner (M3-04).

    The task manifest stays contract v1 (opaque case IDs); this companion reads
    the problem statement, repo/base commit and scoring materials under the
    same attempt-bound short-term token.
    """

    row = session.scalar(
        select(Outbox).where(
            Outbox.topic == SHARD_DISPATCH_TOPIC,
            Outbox.dispatch_key == dispatch_key,
        )
    )
    expected_key = f"{eval_run_id}:{shard_id}:{attempt}"
    if (
        row is None
        or row.dispatch_key != expected_key
        or row.payload_json.get("shard_id") != shard_id
        or int(row.payload_json.get("attempt", -1)) != attempt
    ):
        raise HTTPException(
            status_code=404,
            detail=error_detail("shard_dispatch_not_found", "分片派发记录不存在"),
        )
    run = session.get(EvalRun, row.aggregate_id)
    if run is None:
        raise HTTPException(status_code=404, detail=error_detail("evaluation_not_found", "评测不存在"))
    cases = list(
        session.scalars(
            select(EvalCaseRun)
            .where(EvalCaseRun.eval_run_id == run.id)
            .order_by(EvalCaseRun.ordinal)
        )
    )
    shard_cases = [
        case
        for case in cases
        if session.scalar(
            select(EvalCaseAttempt.id).where(
                EvalCaseAttempt.eval_case_run_id == case.id,
                EvalCaseAttempt.shard_id == shard_id,
                EvalCaseAttempt.attempt_no == attempt,
            )
        )
        is not None
    ]
    if not shard_cases:
        raise HTTPException(status_code=404, detail=error_detail("shard_cases_not_found", "分片没有用例"))
    first_attempt = session.scalar(
        select(EvalCaseAttempt).where(
            EvalCaseAttempt.eval_case_run_id == shard_cases[0].id,
            EvalCaseAttempt.shard_id == shard_id,
            EvalCaseAttempt.attempt_no == attempt,
        )
    )
    _check_token_bindings(token_claims, case_run=shard_cases[0], attempt=first_attempt)
    gateway_base = settings.runner_callback_base_url.rstrip("/")
    dataset_version = session.get(DatasetVersion, run.dataset_version_id) if run.dataset_version_id else None
    dataset = session.get(Dataset, dataset_version.dataset_id) if dataset_version else None
    return {
        "ok": True,
        "eval_run_id": run.id,
        "shard_id": shard_id,
        "attempt": attempt,
        "runtime": {
            "key": run.runtime_key or "claude-code",
            "version": run.runtime_version,
        },
        "model": {
            "name": run.model_name,
            "openai_base_url": f"{gateway_base}/model-gateway/v1",
            "anthropic_base_url": f"{gateway_base}/model-gateway/anthropic",
        },
        "execution_image_digest": run.execution_image_digest,
        "scoring_image_digest": run.scoring_image_digest,
        "environment_image": dataset_version.environment_image if dataset_version else None,
        "adapter_key": dataset.adapter_key if dataset else "swebench",
        "asset_snapshot": asset_service.task_asset_payload(run.asset_snapshot_json, settings),
        "cases": [
            {
                "case_run_id": case.id,
                "case_key": case.case_key,
                "prompt": (case.task_json or {}).get("problem_statement", ""),
                "repo": (case.task_json or {}).get("repo"),
                "base_commit": (case.task_json or {}).get("base_commit"),
                "test_patch": (case.task_json or {}).get("test_patch"),
                "expected_metrics": (case.task_json or {}).get("expected_metrics", {}),
            }
            for case in shard_cases
        ],
    }
