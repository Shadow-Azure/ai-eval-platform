"""M10 (#378): reusable evaluation templates and one-click launch.

A template is a project-level preset that fixes the *selections* of a launch
(dataset version, Agent runtime/assets, model credential, resource pool, case
subset and execution policy). It never freezes results: every launch resolves
the asset ref again, re-runs the existing preparation checks and creates a
normal ``eval_run`` through the same prepare/create path as the wizard, so
capacity, budget, image readiness and permission gates cannot be bypassed.

The three stable execution APIs are:

* ``POST /api/v1/eval-templates/{id}/launch`` (admin, idempotent)
* ``GET /api/v1/eval-runs/{run_id}/status`` (any session)
* ``GET /api/v1/eval-runs/{run_id}/results`` (any session, implemented in
  ``api/results.py`` for the console read model)

References are plain ids rather than database foreign keys: deleting a pool,
dataset or credential is allowed by the owning domain, and the template then
reports an explicit ``template_reference_invalid`` on save/launch instead of
leaking a foreign-key failure.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..agent_timeout import (
    MAX_AGENT_TIMEOUT_SECONDS,
    MIN_AGENT_TIMEOUT_SECONDS,
)
from ..config import Settings, get_settings
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import (
    AgentAssetSnapshot,
    AgentAssetSource,
    Dataset,
    DatasetVersion,
    EvalRun,
    EvalTemplate,
    ModelConfig,
    ResourcePool,
    ServiceToken,
)
from ..money import money_str
from . import agent_assets as asset_service
from . import evaluations as evaluation_service
from . import preparations as preparation_service
from . import service_tokens as token_service
from .git_client import GitClientError

TEMPLATE_STATUSES = ("enabled", "disabled")
TEMPLATE_SEARCH_LIMIT = 100

# M10 (#429): the launch override resolves a candidate asset selection. ``commit``
# is the authoritative identity (40-hex), while ``ref`` is display/audit only.
# The asset domain's own selection errors keep their code so a caller sees the
# same contract as the run-level asset endpoints.
ASSET_OVERRIDE_FIELD = "asset_override"
ASSET_OVERRIDE_PATH_LIMIT = 500
ASSET_OVERRIDE_MAX_URL_LENGTH = 500
ASSET_OVERRIDE_MAX_REF_LENGTH = 200
PASSTHROUGH_ASSET_CODES = frozenset(
    {"asset_path_not_found", "asset_selection_empty", "asset_type_invalid"}
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class TemplateError(Exception):
    """Base error mapped to the console's ``{"error": {...}}`` envelope."""

    status = 422
    code = "template_invalid"

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


class TemplateNotFound(TemplateError):
    status = 404
    code = "template_not_found"


class TemplateDisabled(TemplateError):
    status = 409
    code = "template_disabled"


class TemplateConflict(TemplateError):
    status = 409
    code = "template_version_conflict"


class TemplateInvalid(TemplateError):
    status = 422
    code = "template_invalid"


class TemplateLaunchError(TemplateError):
    """A launch failed after preparation; keep the preparation HTTP mapping."""

    status = 422
    code = "template_launch_blocked"

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        details: dict | None = None,
        code: str | None = None,
        status: int = 422,
    ) -> None:
        super().__init__(message, field=field, details=details, code=code)
        self.status = status


class AssetSourceUnresolved(TemplateError):
    """``asset_override.repo_url`` cannot be matched to a registered source."""

    status = 422
    code = "asset_source_unresolved"


class AssetRefUnresolved(TemplateError):
    """The requested commit/ref cannot be resolved in the asset repository."""

    status = 422
    code = "asset_ref_unresolved"


class CandidateCommitMismatch(TemplateError):
    """The resolved commit differs from the requested candidate commit."""

    status = 422
    code = "candidate_commit_mismatch"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _as_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _text(value: Any, *, field: str, max_length: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise TemplateInvalid(f"{field} 不能为空", field=field)
        return None
    if not isinstance(value, str):
        raise TemplateInvalid(f"{field} 必须是字符串", field=field)
    stripped = value.strip()
    if required and not stripped:
        raise TemplateInvalid(f"{field} 不能为空", field=field)
    if len(stripped) > max_length:
        raise TemplateInvalid(f"{field} 不能超过 {max_length} 个字符", field=field)
    return stripped or None


def _get_tenant_row(session: Session, model, row_id: str | None, *, field: str):
    if not row_id:
        return None
    row = session.get(model, row_id)
    if row is None or getattr(row, "tenant_id", None) != str(DEFAULT_TENANT_ID):
        return None
    return row


def _require_version(session: Session, dataset_version_id: str) -> DatasetVersion:
    version = _get_tenant_row(
        session, DatasetVersion, dataset_version_id, field="dataset_version_id"
    )
    if version is None:
        raise TemplateInvalid("数据集版本不存在或已删除", field="dataset_version_id")
    if version.status != "active" or version.case_count <= 0:
        raise TemplateInvalid("数据集版本不可用", field="dataset_version_id")
    return version


def _require_model(session: Session, model_config_id: str) -> ModelConfig:
    config = _get_tenant_row(session, ModelConfig, model_config_id, field="model_config_id")
    if config is None:
        raise TemplateInvalid("模型凭据不存在或已删除", field="model_config_id")
    return config


def _require_pool(session: Session, resource_pool_id: str) -> ResourcePool:
    pool = _get_tenant_row(session, ResourcePool, resource_pool_id, field="resource_pool_id")
    if pool is None:
        raise TemplateInvalid("资源池不存在或已删除", field="resource_pool_id")
    return pool


def _require_source(session: Session, asset_source_id: str) -> AgentAssetSource:
    source = _get_tenant_row(
        session, AgentAssetSource, asset_source_id, field="asset_source_id"
    )
    if source is None:
        raise TemplateInvalid("Agent 资产源不存在或已删除", field="asset_source_id")
    return source


def _normalise_case_selection(
    session: Session, value: dict | None, version: DatasetVersion
) -> dict:
    raw = value if isinstance(value, dict) else {}
    mode = raw.get("mode") or "all"
    if mode not in ("all", "count", "cases"):
        raise TemplateInvalid("用例范围模式只能是 all、count 或 cases", field="case_selection")
    case_count = max(0, int(version.case_count or 0))
    if mode == "all":
        return {"mode": "all", "count": None, "case_keys": None}
    if mode == "count":
        count = raw.get("count")
        if not isinstance(count, int) or count < 1:
            raise TemplateInvalid("count 模式必须提供正整数 count", field="case_selection")
        if case_count and count > case_count:
            raise TemplateInvalid(
                f"count 不能超过数据集版本的 {case_count} 个用例", field="case_selection"
            )
        return {"mode": "count", "count": count, "case_keys": None}
    keys = raw.get("case_keys")
    if not isinstance(keys, list) or not keys:
        raise TemplateInvalid("cases 模式必须提供非空 case_keys", field="case_selection")
    cleaned: list[str] = []
    for key in keys:
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise TemplateInvalid("case_key 必须是 1..200 字符的字符串", field="case_selection")
        cleaned.append(key)
    if len(set(cleaned)) != len(cleaned):
        raise TemplateInvalid("case_keys 不能重复", field="case_selection")
    return {"mode": "cases", "count": None, "case_keys": cleaned}


def _normalise_execution(value: dict | None, settings: Settings) -> dict:
    raw = value if isinstance(value, dict) else {}
    concurrency = raw.get("concurrency")
    if concurrency is None:
        concurrency = settings.eval_default_requested_concurrency
    if not isinstance(concurrency, int) or not 1 <= concurrency <= 256:
        raise TemplateInvalid("并发上限必须在 1..256 之间", field="execution")
    result: dict[str, Any] = {"concurrency": concurrency}
    case_timeout = raw.get("case_timeout_seconds")
    if case_timeout is not None:
        if not isinstance(case_timeout, int) or not 1 <= case_timeout <= 604800:
            raise TemplateInvalid("单用例超时必须在 1..604800 秒之间", field="execution")
        result["case_timeout_seconds"] = case_timeout
    max_attempts = raw.get("max_attempts")
    if max_attempts is not None:
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise TemplateInvalid("最大 attempt 数必须在 1..10 之间", field="execution")
        result["max_attempts"] = max_attempts
    budget = raw.get("model_budget_cny")
    if budget is not None:
        try:
            numeric = float(budget)
        except (TypeError, ValueError) as error:
            raise TemplateInvalid("模型预算必须是数字", field="execution") from error
        if numeric < 0:
            raise TemplateInvalid("模型预算不能为负", field="execution")
        result["model_budget_cny"] = numeric
    shard_size = raw.get("shard_size")
    if shard_size is not None:
        if not isinstance(shard_size, int) or not 1 <= shard_size <= 255:
            raise TemplateInvalid("分片大小必须在 1..255 之间", field="execution")
        result["shard_size"] = shard_size
    return result


def _effective_digest(*candidates: Any) -> str | None:
    """Resolve a digest by precedence: override → dataset version → platform default."""

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _image_dependency_blockers(
    version: DatasetVersion | None,
    execution: dict | None,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Deterministic launch blockers that a template save can already reject.

    Asset resolution, capacity and credentials depend on live state, so they
    stay in the launch preflight. Image digests only depend on the stored
    override, the bound dataset version and the platform defaults, so a
    template that would certainly be blocked at launch must not be saved.
    """

    resolved_execution = execution if isinstance(execution, dict) else {}
    version_label = version.version if version is not None else "未绑定数据集版本"
    blockers: list[dict[str, Any]] = []
    for key, code, message in (
        ("execution_image_digest", "execution_image_missing", "执行镜像 digest 缺失"),
        ("scoring_image_digest", "scoring_image_missing", "评分镜像 digest 缺失"),
    ):
        digest = _effective_digest(
            resolved_execution.get(key),
            getattr(version, key, None) if version is not None else None,
            getattr(settings, f"default_{key}", None),
        )
        if digest:
            continue
        blockers.append(
            {
                "code": code,
                "message": message,
                "field": f"execution.{key}",
                "details": {"dataset_version": version_label},
            }
        )
    return blockers


def _normalise_asset_selection(
    source: AgentAssetSource | None,
    asset_ref: str | None,
    asset_paths: list | None,
    asset_types: dict | None,
) -> tuple[str | None, list[str], dict[str, str]]:
    if source is None:
        return None, [], {}
    ref = (asset_ref or source.default_ref or "").strip()
    if not ref:
        raise TemplateInvalid("资产 Ref 未解析：请填写 Ref 或为资产源设置默认 Ref", field="asset_ref")
    paths: list[str] = []
    if asset_paths is not None:
        if not isinstance(asset_paths, list):
            raise TemplateInvalid("asset_paths 必须是字符串列表", field="asset_paths")
        for path in asset_paths:
            if not isinstance(path, str) or not path.strip() or len(path) > 500:
                raise TemplateInvalid("asset_path 必须是 1..500 字符的字符串", field="asset_paths")
            paths.append(path)
    types: dict[str, str] = {}
    if asset_types is not None:
        if not isinstance(asset_types, dict):
            raise TemplateInvalid("asset_types 必须是对象", field="asset_types")
        types = {
            str(key): str(value)
            for key, value in asset_types.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    return ref, paths, types


def _self_evolution_columns(
    config: dict | None,
    *,
    existing_ciphertext: str | None = None,
    require_token: bool = False,
) -> dict[str, Any]:
    """Validate a template reporting target into ``EvalTemplate`` columns.

    The stored form mirrors the run-level columns: only the enabled flag and
    the two non-secret target fields are directly readable, while the token is
    Fernet ciphertext. An update that omits the token keeps the existing
    ciphertext; disabling clears every target field and the ciphertext.
    """

    try:
        view, token = preparation_service.normalise_self_evolution(
            config, require_token=require_token
        )
        if not view["enabled"]:
            ciphertext = None
        elif token:
            ciphertext = preparation_service.encrypt_self_evolution_token(token)
        elif existing_ciphertext:
            ciphertext = existing_ciphertext
        else:
            # An update may omit the token only when it already has one to
            # keep; enabling from scratch without a token would create a
            # template that can never report, so reject it here instead of
            # deferring the failure to launch time.
            raise TemplateInvalid(
                "启用自进化上报时必须提供上报令牌", field="self_evolution"
            )
    except preparation_service.PreparationInvalid as error:
        raise TemplateInvalid(str(error), field="self_evolution") from error
    return {
        "self_evolution_enabled": view["enabled"],
        "self_evolution_base_url": view["base_url"],
        "self_evolution_workspace_id": view["workspace_id"],
        "self_evolution_token_encrypted": ciphertext,
    }


def _validate_fields(
    session: Session,
    data: dict,
    settings: Settings,
    *,
    validate_image_dependencies: bool = True,
) -> dict:
    name = _text(data.get("name"), field="name", max_length=200, required=True)
    description = _text(data.get("description"), field="description", max_length=1000)
    if not data.get("dataset_version_id"):
        raise TemplateInvalid("dataset_version_id 不能为空", field="dataset_version_id")
    version = _require_version(session, str(data["dataset_version_id"]))
    if not data.get("model_config_id"):
        raise TemplateInvalid("model_config_id 不能为空", field="model_config_id")
    config = _require_model(session, str(data["model_config_id"]))
    if not data.get("resource_pool_id"):
        raise TemplateInvalid("resource_pool_id 不能为空", field="resource_pool_id")
    pool = _require_pool(session, str(data["resource_pool_id"]))
    source = None
    if data.get("asset_source_id"):
        source = _require_source(session, str(data["asset_source_id"]))
    asset_ref, asset_paths, asset_types = _normalise_asset_selection(
        source, data.get("asset_ref"), data.get("asset_paths"), data.get("asset_types")
    )
    case_selection = _normalise_case_selection(session, data.get("case_selection"), version)
    execution = _normalise_execution(data.get("execution"), settings)
    image_blockers = (
        _image_dependency_blockers(version, execution, settings)
        if validate_image_dependencies
        else []
    )
    if image_blockers:
        raise TemplateInvalid(
            "模板保存前镜像依赖校验未通过："
            + "、".join(blocker["message"] for blocker in image_blockers),
            code="template_save_blocked",
            details={
                "blockers": image_blockers,
                "fields": {blocker["field"]: "invalid" for blocker in image_blockers},
            },
        )
    prompt_suffix = data.get("prompt_suffix")
    if prompt_suffix is not None:
        if not isinstance(prompt_suffix, str) or len(prompt_suffix) > 4000:
            raise TemplateInvalid("附加指令不能超过 4000 个字符", field="prompt_suffix")
        prompt_suffix = prompt_suffix.strip()
    max_turns = data.get("max_turns")
    if max_turns is not None:
        if (
            not isinstance(max_turns, int)
            or not 1 <= max_turns <= evaluation_service.MAX_TURNS_LIMIT
        ):
            raise TemplateInvalid(
                f"最大轮次必须在 1..{evaluation_service.MAX_TURNS_LIMIT} 之间",
                field="max_turns",
            )
    agent_timeout = data.get("agent_timeout_seconds")
    if agent_timeout is not None:
        if (
            not isinstance(agent_timeout, int)
            or not MIN_AGENT_TIMEOUT_SECONDS <= agent_timeout <= MAX_AGENT_TIMEOUT_SECONDS
        ):
            raise TemplateInvalid(
                "Agent 超时必须在 "
                f"{MIN_AGENT_TIMEOUT_SECONDS}..{MAX_AGENT_TIMEOUT_SECONDS} 秒之间",
                field="agent_timeout_seconds",
            )
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise TemplateInvalid("enabled 必须是布尔值", field="enabled")
    return {
        "name": name,
        "description": description,
        "dataset_version_id": version.id,
        "runtime_key": (data.get("runtime_key") or "claude-code"),
        "runtime_version": (data.get("runtime_version") or None),
        "asset_source_id": source.id if source is not None else None,
        "asset_ref": asset_ref,
        "asset_paths_json": asset_paths,
        "asset_types_json": asset_types,
        "model_config_id": config.id,
        "model_name": config.model_name,
        "resource_pool_id": pool.id,
        "case_selection_json": case_selection,
        "execution_json": execution,
        "prompt_suffix": prompt_suffix or "",
        "max_turns": max_turns,
        "agent_timeout_seconds": agent_timeout,
        "enabled": True if enabled is None else enabled,
    }


def create_template(
    session: Session,
    data: dict,
    *,
    created_by: str,
    settings: Settings | None = None,
) -> EvalTemplate:
    settings = settings or get_settings()
    fields = _validate_fields(session, data, settings)
    fields.update(
        _self_evolution_columns(data.get("self_evolution"), require_token=True)
    )
    template = EvalTemplate(
        tenant_id=str(DEFAULT_TENANT_ID),
        project_id=str(DEFAULT_PROJECT_ID),
        created_by=created_by[:128],
        version=1,
        **fields,
    )
    session.add(template)
    session.commit()
    return template


def get_template(session: Session, template_id: str) -> EvalTemplate:
    template = session.scalar(
        select(EvalTemplate).where(
            EvalTemplate.id == template_id,
            EvalTemplate.tenant_id == str(DEFAULT_TENANT_ID),
        )
    )
    if template is None:
        raise TemplateNotFound("评测模板不存在")
    return template


def update_template(
    session: Session,
    template_id: str,
    changes: dict,
    *,
    expected_version: int | None = None,
    settings: Settings | None = None,
) -> EvalTemplate:
    settings = settings or get_settings()
    template = get_template(session, template_id)
    if expected_version is not None and expected_version != template.version:
        raise TemplateConflict(
            f"模板已被更新（当前版本 {template.version}），请刷新后重试",
            details={"current_version": template.version},
        )
    current = {
        "name": template.name,
        "description": template.description,
        "dataset_version_id": template.dataset_version_id,
        "runtime_key": template.runtime_key,
        "runtime_version": template.runtime_version,
        "asset_source_id": template.asset_source_id,
        "asset_ref": template.asset_ref,
        "asset_paths": list(template.asset_paths_json or []),
        "asset_types": dict(template.asset_types_json or {}),
        "model_config_id": template.model_config_id,
        "resource_pool_id": template.resource_pool_id,
        "case_selection": template.case_selection_json or {},
        "execution": template.execution_json or {},
        "prompt_suffix": template.prompt_suffix,
        "max_turns": template.max_turns,
        "agent_timeout_seconds": template.agent_timeout_seconds,
        "enabled": template.enabled,
    }
    for key in (
        "name",
        "description",
        "dataset_version_id",
        "runtime_key",
        "runtime_version",
        "asset_source_id",
        "asset_ref",
        "asset_paths",
        "asset_types",
        "model_config_id",
        "resource_pool_id",
        "case_selection",
        "execution",
        "prompt_suffix",
        "max_turns",
        "agent_timeout_seconds",
        "enabled",
    ):
        if key in changes:
            current[key] = changes[key]
    # A rename, description edit or enable/disable must not be trapped by an
    # image dependency that was already missing before the request. Only a
    # change to the launch fields that can alter digest resolution re-checks
    # the deterministic save-time gate.
    fields = _validate_fields(
        session,
        current,
        settings,
        validate_image_dependencies=(
            "dataset_version_id" in changes or "execution" in changes
        ),
    )
    # ``self_evolution`` is patched as a whole: an omitted target (or an
    # explicit null) keeps the stored config/ciphertext, while a provided
    # target replaces every field. Its token stays optional on update.
    if changes.get("self_evolution") is not None:
        fields.update(
            _self_evolution_columns(
                changes["self_evolution"],
                existing_ciphertext=template.self_evolution_token_encrypted,
                require_token=False,
            )
        )
    for key, value in fields.items():
        setattr(template, key, value)
    template.version = int(template.version or 1) + 1
    session.commit()
    return template


def delete_template(session: Session, template_id: str) -> None:
    """Delete only the template row; started evaluations and traces stay.

    ``EvalRun.template_id`` is a provenance string without a foreign key, so a
    run keeps its template id and version after the template is gone. The
    template name simply stops resolving in evaluation summaries and detail
    pages; nothing in the execution or trace chain is removed.
    """

    template = get_template(session, template_id)
    session.delete(template)
    session.commit()


def _search_conditions(q: str | None, status: str | None, dataset_version_id: str | None):
    conditions = []
    if status:
        if status not in TEMPLATE_STATUSES:
            raise TemplateInvalid(
                f"未知模板状态：{status}", field="status", code="template_invalid"
            )
        conditions.append(EvalTemplate.enabled.is_(status == "enabled"))
    if dataset_version_id:
        conditions.append(EvalTemplate.dataset_version_id == dataset_version_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(
                EvalTemplate.name.ilike(pattern),
                EvalTemplate.description.ilike(pattern),
                EvalTemplate.created_by.ilike(pattern),
                EvalTemplate.runtime_key.ilike(pattern),
                EvalTemplate.model_name.ilike(pattern),
                Dataset.name.ilike(pattern),
                DatasetVersion.version.ilike(pattern),
                ModelConfig.provider.ilike(pattern),
                ModelConfig.display_name.ilike(pattern),
                ModelConfig.model_name.ilike(pattern),
                ResourcePool.name.ilike(pattern),
                AgentAssetSource.name.ilike(pattern),
            )
        )
    return conditions


def _select_columns():
    return (
        EvalTemplate,
        DatasetVersion,
        Dataset,
        ModelConfig,
        ResourcePool,
        AgentAssetSource,
    )


def list_templates(
    session: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    q: str | None = None,
    status: str | None = None,
    dataset_version_id: str | None = None,
) -> tuple[list[EvalTemplate], int]:
    limit = max(1, min(int(limit), TEMPLATE_SEARCH_LIMIT))
    offset = max(0, int(offset))
    conditions = _search_conditions(q, status, dataset_version_id)
    statement = (
        select(*_select_columns())
        .select_from(EvalTemplate)
        .outerjoin(DatasetVersion, DatasetVersion.id == EvalTemplate.dataset_version_id)
        .outerjoin(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .outerjoin(ModelConfig, ModelConfig.id == EvalTemplate.model_config_id)
        .outerjoin(ResourcePool, ResourcePool.id == EvalTemplate.resource_pool_id)
        .outerjoin(AgentAssetSource, AgentAssetSource.id == EvalTemplate.asset_source_id)
        .where(*conditions)
        .order_by(
            EvalTemplate.last_launched_at.desc().nulls_last(),
            EvalTemplate.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    rows = session.execute(statement).all()
    count_statement = (
        select(func.count())
        .select_from(EvalTemplate)
        .outerjoin(DatasetVersion, DatasetVersion.id == EvalTemplate.dataset_version_id)
        .outerjoin(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .outerjoin(ModelConfig, ModelConfig.id == EvalTemplate.model_config_id)
        .outerjoin(ResourcePool, ResourcePool.id == EvalTemplate.resource_pool_id)
        .outerjoin(AgentAssetSource, AgentAssetSource.id == EvalTemplate.asset_source_id)
        .where(*conditions)
    )
    total = int(session.scalar(count_statement) or 0)
    return [row[0] for row in rows], total


def _case_count(
    template: EvalTemplate, version: DatasetVersion | None
) -> tuple[int, int]:
    total = int(version.case_count or 0) if version is not None else 0
    selection = template.case_selection_json or {}
    mode = selection.get("mode") or "all"
    if mode == "count":
        return min(int(selection.get("count") or 0), total), total
    if mode == "cases":
        return len(selection.get("case_keys") or []), total
    return total, total


def template_view(
    session: Session,
    template: EvalTemplate,
    *,
    settings: Settings | None = None,
    include_recent: bool = False,
    recent_limit: int = 5,
) -> dict:
    settings = settings or get_settings()
    version = _get_tenant_row(session, DatasetVersion, template.dataset_version_id, field="")
    dataset = (
        _get_tenant_row(session, Dataset, version.dataset_id, field="")
        if version is not None
        else None
    )
    config = _get_tenant_row(session, ModelConfig, template.model_config_id, field="")
    pool = _get_tenant_row(session, ResourcePool, template.resource_pool_id, field="")
    source = (
        _get_tenant_row(session, AgentAssetSource, template.asset_source_id, field="")
        if template.asset_source_id
        else None
    )
    case_count, dataset_case_count = _case_count(template, version)
    execution = template.execution_json or {}
    estimate = None
    if config is not None:
        estimate = preparation_service.estimate_cost(
            pricing=config.pricing_json or {},
            case_count=case_count,
            effective_concurrency=int(execution.get("concurrency") or 1),
            pool=pool,
            settings=settings,
        )
    asset_paths = list(template.asset_paths_json or [])
    image_blockers = _image_dependency_blockers(version, execution, settings)
    view: dict[str, Any] = {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "enabled": bool(template.enabled),
        "version": template.version,
        "launch_count": template.launch_count,
        "last_launched_at": _iso(template.last_launched_at),
        "created_by": template.created_by,
        "created_at": _iso(template.created_at),
        "updated_at": _iso(template.updated_at),
        "dataset_version_id": template.dataset_version_id,
        "dataset_id": dataset.id if dataset is not None else None,
        "dataset_name": dataset.name if dataset is not None else None,
        "dataset_version": version.version if version is not None else None,
        "dataset_case_count": dataset_case_count,
        "case_count": case_count,
        "case_selection": template.case_selection_json or {"mode": "all"},
        "runtime_key": template.runtime_key,
        "runtime_version": template.runtime_version,
        "asset_source_id": template.asset_source_id,
        "asset_source_name": source.name if source is not None else None,
        "asset_repo_url": source.repo_url if source is not None else None,
        "asset_ref": template.asset_ref,
        "asset_paths": asset_paths,
        "asset_path_count": len(asset_paths),
        "asset_types": template.asset_types_json or {},
        "model_config_id": template.model_config_id,
        "model_provider": config.provider if config is not None else None,
        "model_display_name": config.display_name if config is not None else None,
        "model_name": config.model_name if config is not None else template.model_name,
        "resource_pool_id": template.resource_pool_id,
        "resource_pool_name": pool.name if pool is not None else None,
        "resource_pool_max_capacity": (
            int(pool.max_capacity) if pool is not None and pool.max_capacity is not None else None
        ),
        "resource_pool_price_cny": (
            money_str(pool.price_cny) if pool is not None and pool.price_cny is not None else None
        ),
        "execution": execution,
        "prompt_suffix": template.prompt_suffix or "",
        "max_turns": template.max_turns,
        "agent_timeout_seconds": template.agent_timeout_seconds,
        # M8 (#382): the write-only reporting target, same shape as the run
        # detail: the token itself never leaves the encrypted column.
        "self_evolution": {
            "enabled": bool(template.self_evolution_enabled),
            "base_url": template.self_evolution_base_url,
            "workspace_id": template.self_evolution_workspace_id,
            "has_token": bool(template.self_evolution_token_encrypted),
        },
        "estimated_cost": estimate,
        # #398: deterministic status for existing templates. Only the image
        # dependency is known without a live check, so a template blocked here
        # recovers automatically once the version or platform default carries
        # both digests; no template edit is required.
        "preflight": {
            "status": "blocked" if image_blockers else "ready",
            "blockers": image_blockers,
        },
        "status_url": "/api/v1/eval-runs/{run_id}/status",
        "result_url": "/api/v1/eval-runs/{run_id}/results",
    }
    if include_recent:
        view["recent_runs"] = _recent_runs(session, template.id, limit=recent_limit)
    return view


def template_briefs(
    session: Session, template_ids: list[str | None]
) -> dict[str, dict]:
    """Resolve visible template briefs for a page of evaluation summaries.

    One batched query keeps the evaluation list free of per-row template
    requests; ids that are missing or belong to another tenant stay unresolved
    so a run keeps its recorded ``template_id`` without inventing a label.
    """

    ids = {template_id for template_id in template_ids if template_id}
    if not ids:
        return {}
    templates = session.scalars(
        select(EvalTemplate).where(
            EvalTemplate.id.in_(ids),
            EvalTemplate.tenant_id == str(DEFAULT_TENANT_ID),
        )
    )
    return {
        template.id: {
            "id": template.id,
            "name": template.name,
            "version": template.version,
            "enabled": bool(template.enabled),
        }
        for template in templates
    }


def template_brief(session: Session, template_id: str | None) -> dict | None:
    if not template_id:
        return None
    return template_briefs(session, [template_id]).get(template_id)


def _recent_runs(session: Session, template_id: str, *, limit: int = 5) -> list[dict]:
    runs = list(
        session.scalars(
            select(EvalRun)
            .where(
                EvalRun.template_id == template_id,
                EvalRun.tenant_id == str(DEFAULT_TENANT_ID),
            )
            .order_by(EvalRun.created_at.desc())
            .limit(max(1, limit))
        )
    )
    result = []
    totals = evaluation_service.evaluation_case_totals(session, [run.id for run in runs])
    for run in runs:
        resolved, scored = totals.get(run.id, (0, 0))
        summary = evaluation_service.evaluation_summary(
            run, resolved_count=resolved, scored_count=scored
        )
        result.append(
            {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "template_version": run.template_version,
                "total_cases": run.total_cases,
                "resolved_count": summary.get("resolved_count"),
                "scored_count": summary.get("scored_count"),
                "resolve_rate": summary.get("resolve_rate"),
                "created_at": _iso(run.created_at),
                "started_at": _iso(run.started_at),
                "finished_at": _iso(run.finished_at),
                "duration_seconds": summary.get("duration_seconds"),
            }
        )
    return result


def _resolve_template_assets(
    session: Session,
    template: EvalTemplate,
    *,
    created_by: str,
    settings: Settings,
) -> str | None:
    if not template.asset_source_id:
        return None
    try:
        source = _require_source(session, template.asset_source_id)
        snapshot, _assets, _created = asset_service.resolve_assets(
            session,
            source_id=source.id,
            requested_ref=template.asset_ref or source.default_ref,
            paths=list(template.asset_paths_json or []),
            types=dict(template.asset_types_json or {}),
            runtime_key=template.runtime_key,
            created_by=created_by,
            settings=settings,
        )
    except TemplateError:
        raise
    except Exception as error:  # AgentAssetError | GitClientError | network failure
        raise TemplateInvalid(
            f"资产 Ref 解析失败：{error}",
            field="asset_ref",
            code="template_reference_invalid",
        ) from error
    return snapshot.id


def _preparation_error(error: Exception) -> TemplateLaunchError:
    details = dict(getattr(error, "details", None) or {})
    status = int(getattr(error, "status", 422) or 422)
    code = str(getattr(error, "code", "") or "template_launch_blocked")
    if code in ("evaluation_invalid", "preparation_invalid"):
        code = "template_launch_blocked"
        status = 422
    if isinstance(error, preparation_service.PreparationBlocked):
        details["blockers"] = error.blockers
        fields = {
            blocker["field"]: "invalid"
            for blocker in error.blockers
            if blocker.get("field")
        }
        if fields:
            details["fields"] = fields
        code = "template_launch_blocked"
        status = 422
    if isinstance(error, preparation_service.ConfigurationChanged):
        details["changed"] = error.changed
    field = getattr(error, "field", None)
    return TemplateLaunchError(
        str(error) or "模板启动失败",
        field=field,
        status=status,
        code=code,
        details=details,
    )


def _self_evolution_launch_payload(template: EvalTemplate) -> dict:
    """Resolve the frozen reporting target for a template launch.

    A disabled template passes ``{"enabled": False}``. An enabled template
    must still hold a complete target and a decryptable token; a half-cleared
    or corrupted row is a 422 before any run exists instead of a launch that
    silently stops reporting bad cases.
    """

    if not template.self_evolution_enabled:
        return {"enabled": False}
    if not (
        template.self_evolution_base_url
        and template.self_evolution_workspace_id
        and template.self_evolution_token_encrypted
    ):
        raise TemplateInvalid(
            "模板已启用自进化上报，但缺少平台地址、工作区或令牌；请更新模板后重试",
            field="self_evolution",
        )
    try:
        token = preparation_service.decrypt_self_evolution_token(
            template.self_evolution_token_encrypted
        )
    except preparation_service.PreparationInvalid as error:
        raise TemplateInvalid(
            "模板自进化上报令牌无法解密，请重新保存模板",
            field="self_evolution",
        ) from error
    return {
        "enabled": True,
        "base_url": template.self_evolution_base_url,
        "workspace_id": template.self_evolution_workspace_id,
        "token": token,
    }


def _override_text(override: dict, key: str, *, max_length: int) -> str | None:
    """Read one optional override string; ``None`` means the field is absent."""

    value = override.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TemplateInvalid(
            f"asset_override.{key} 必须是字符串", field=ASSET_OVERRIDE_FIELD
        )
    text = value.strip()
    if len(text) > max_length:
        raise TemplateInvalid(
            f"asset_override.{key} 不能超过 {max_length} 个字符",
            field=ASSET_OVERRIDE_FIELD,
        )
    return text or None


def _override_paths(override: dict) -> list[str] | None:
    """Whole-field ``asset_paths``; ``None`` inherits the template value."""

    value = override.get("asset_paths")
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise TemplateInvalid(
            "asset_override.asset_paths 必须是字符串列表", field=ASSET_OVERRIDE_FIELD
        )
    paths: list[str] = []
    for path in value:
        if (
            not isinstance(path, str)
            or not path.strip()
            or len(path) > ASSET_OVERRIDE_PATH_LIMIT
        ):
            raise TemplateInvalid(
                "asset_override.asset_paths 每项必须是 "
                f"1..{ASSET_OVERRIDE_PATH_LIMIT} 字符的字符串",
                field=ASSET_OVERRIDE_FIELD,
            )
        paths.append(path)
    return paths


def _override_types(override: dict) -> dict[str, str] | None:
    """Whole-field ``asset_types``; ``None`` inherits the template value."""

    value = override.get("asset_types")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TemplateInvalid(
            "asset_override.asset_types 必须是对象", field=ASSET_OVERRIDE_FIELD
        )
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _override_source(
    session: Session, template: EvalTemplate, override: dict
) -> AgentAssetSource:
    """Resolve the asset source an override pins its commit in.

    ``repo_url`` must exactly match a registered tenant source, because only a
    registered source carries the credential and scan paths used to read the
    candidate commit. Without ``repo_url`` the template's own source is used.
    """

    repo_url = _override_text(
        override, "repo_url", max_length=ASSET_OVERRIDE_MAX_URL_LENGTH
    )
    if repo_url:
        source = session.scalar(
            select(AgentAssetSource)
            .where(
                AgentAssetSource.tenant_id == str(DEFAULT_TENANT_ID),
                AgentAssetSource.repo_url == repo_url,
            )
            .order_by(AgentAssetSource.created_at, AgentAssetSource.id)
        )
        if source is None:
            raise AssetSourceUnresolved(
                f"资产覆盖的仓库未登记为资产源：{repo_url}",
                field=ASSET_OVERRIDE_FIELD,
                details={"field": ASSET_OVERRIDE_FIELD, "repo_url": repo_url},
            )
        return source
    if not template.asset_source_id:
        raise AssetSourceUnresolved(
            "资产覆盖未指定 repo_url，且模板未绑定资产源",
            field=ASSET_OVERRIDE_FIELD,
            details={"field": ASSET_OVERRIDE_FIELD, "template_id": template.id},
        )
    source = _get_tenant_row(
        session, AgentAssetSource, template.asset_source_id, field="asset_source_id"
    )
    if source is None:
        raise AssetSourceUnresolved(
            "模板绑定的资产源不存在或已删除",
            field=ASSET_OVERRIDE_FIELD,
            details={
                "field": ASSET_OVERRIDE_FIELD,
                "template_id": template.id,
                "asset_source_id": template.asset_source_id,
            },
        )
    return source


def launch_asset_identity(effective: dict | None) -> dict | None:
    """The 202 ``asset`` block built from a normalized effective identity."""

    if not effective:
        return None
    return {
        "repo_url": effective.get("repo_url"),
        "requested_ref": effective.get("requested_ref"),
        "requested_commit": effective.get("requested_commit"),
        "resolved_commit": effective.get("resolved_commit"),
        "asset_snapshot_id": effective.get("asset_snapshot_id"),
        "bundle_digest": effective.get("bundle_digest"),
    }


def run_asset_identity(session: Session, run: EvalRun) -> dict | None:
    """The stable status/results ``asset`` echo for an asset-frozen run.

    Reads the snapshot referenced by ``run.asset_snapshot_id`` so the echo
    always reflects the frozen identity, never the live source or template.
    Synthetic runs and rows whose snapshot no longer resolves return ``None``
    so both read models keep their previous shape for them.
    """

    snapshot_id = run.asset_snapshot_id
    if not snapshot_id:
        return None
    snapshot = session.get(AgentAssetSnapshot, snapshot_id)
    if snapshot is None or snapshot.tenant_id != str(DEFAULT_TENANT_ID):
        return None
    effective = (run.asset_override_json or {}).get("effective") or {}
    return {
        "resolved_commit": snapshot.resolved_commit,
        "asset_snapshot_id": snapshot.id,
        # A stored override echo wins: ``commit`` only launches report the
        # commit here, while the snapshot itself recorded the resolution ref.
        "requested_ref": effective.get("requested_ref") or snapshot.requested_ref,
    }


def _resolve_override_assets(
    session: Session,
    template: EvalTemplate,
    override: dict,
    *,
    created_by: str,
    settings: Settings,
) -> tuple[str, dict, dict]:
    """Resolve an explicit ``asset_override`` into a frozen snapshot.

    Returns ``(snapshot_id, launch_asset, effective)``. ``commit`` is the
    authoritative identity: it is resolved as-is and the resulting commit is
    asserted before any preparation exists, so a drifting ref or a replaced
    object can never be silently evaluated (``candidate_commit_mismatch``).
    ``ref`` only shapes the audit echo.
    """

    source = _override_source(session, template, override)
    commit = _override_text(override, "commit", max_length=40)
    if commit is not None and _COMMIT_PATTERN.fullmatch(commit) is None:
        raise TemplateInvalid(
            "asset_override.commit 必须是 40 位小写十六进制 commit",
            field=ASSET_OVERRIDE_FIELD,
        )
    display_ref = _override_text(
        override, "ref", max_length=ASSET_OVERRIDE_MAX_REF_LENGTH
    )
    resolution_ref = (
        commit
        or display_ref
        or (template.asset_ref or source.default_ref or "").strip()
    )
    if not resolution_ref:
        raise AssetRefUnresolved(
            "资产 Ref 未解析：请提供 commit/ref，或为模板/资产源设置 Ref",
            field=ASSET_OVERRIDE_FIELD,
            details={"field": ASSET_OVERRIDE_FIELD, "requested_ref": None},
        )
    raw_ref = override.get("ref")
    if isinstance(raw_ref, str) and raw_ref.strip():
        # Display/audit only: echo the request verbatim, never the resolution.
        requested_ref: str | None = raw_ref
    elif commit:
        requested_ref = commit
    else:
        requested_ref = resolution_ref
    paths = _override_paths(override)
    types = _override_types(override)
    if paths is None:
        paths = list(template.asset_paths_json or [])
    if types is None:
        types = dict(template.asset_types_json or {})
    try:
        snapshot, _assets, _snapshot_created = asset_service.resolve_assets(
            session,
            source_id=source.id,
            requested_ref=resolution_ref,
            paths=paths,
            types=types,
            runtime_key=template.runtime_key,
            created_by=created_by,
            settings=settings,
        )
    except asset_service.AgentAssetError as error:
        if error.code in PASSTHROUGH_ASSET_CODES:
            raise TemplateInvalid(
                str(error),
                field=ASSET_OVERRIDE_FIELD,
                details=dict(error.details or {}),
                code=error.code,
            ) from error
        raise TemplateInvalid(
            f"资产解析失败：{error}",
            field=ASSET_OVERRIDE_FIELD,
            code="template_reference_invalid",
        ) from error
    except GitClientError as error:
        raise AssetRefUnresolved(
            f"资产 Ref 解析失败：{error}",
            field=ASSET_OVERRIDE_FIELD,
            details={
                "field": ASSET_OVERRIDE_FIELD,
                "requested_ref": resolution_ref,
                "git_code": error.code,
            },
        ) from error
    except TemplateError:
        raise
    except Exception as error:  # network or object-store failure after resolve
        raise TemplateInvalid(
            f"资产解析失败：{error}",
            field=ASSET_OVERRIDE_FIELD,
            code="template_reference_invalid",
        ) from error
    if commit and snapshot.resolved_commit != commit:
        raise CandidateCommitMismatch(
            f"候选 commit 解析不一致：请求 {commit}，解析 {snapshot.resolved_commit}",
            field=ASSET_OVERRIDE_FIELD,
            details={"requested": commit, "resolved": snapshot.resolved_commit},
        )
    effective = {
        "repo_url": source.repo_url,
        "source_id": source.id,
        "requested_ref": requested_ref,
        "requested_commit": commit,
        "resolved_commit": snapshot.resolved_commit,
        "asset_snapshot_id": snapshot.id,
        "bundle_digest": snapshot.bundle_digest,
        "asset_paths": list(paths),
        "asset_types": dict(types),
    }
    return snapshot.id, launch_asset_identity(effective), effective


def _override_warnings(template: EvalTemplate) -> list[str]:
    """Non-blocking D5 hint when a candidate launch hits a reporting template."""

    if not template.self_evolution_enabled:
        return []
    return [
        "该模板已开启自进化 bad case 上报；候选 commit 评测会向模板工作区回传"
        "失败用例，建议为候选评测使用关闭上报的模板。"
    ]


def _replay_launch_meta(template: EvalTemplate, run: EvalRun) -> dict:
    """Echo a replayed launch from the stored run; no asset work happens."""

    effective = (run.asset_override_json or {}).get("effective") or {}
    if not effective:
        return {"asset": None, "warnings": []}
    return {
        "asset": launch_asset_identity(effective),
        "warnings": _override_warnings(template),
    }


def launch_template(
    session: Session,
    template_id: str,
    *,
    idempotency_key: str,
    created_by: str,
    name: str | None = None,
    asset_override: dict | None = None,
    lineage: dict | None = None,
    service_token: ServiceToken | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, bool, dict]:
    """Launch a template through the existing prepare/create path.

    Returns ``(run, created, launch_meta)``. A replayed idempotency key returns
    the original run of the same template; reusing a key for a different
    template is a 409.

    M10 (#429): a machine caller passes ``service_token`` so its concurrency and
    budget caps are enforced before any asset work, and may pin the run to a
    candidate commit through ``asset_override``. ``launch_meta`` carries the
    202 ``asset`` block plus non-blocking warnings; the console one-click path
    (no override) leaves both empty.
    """

    settings = settings or get_settings()
    now = _now(now)
    key = (idempotency_key or "").strip()
    if not key:
        raise TemplateInvalid("启动必须提供幂等键", field="idempotency_key")
    key = key[:128]
    template = get_template(session, template_id)
    # Replay wins over every live check: a client that retries the same
    # idempotent launch after the template was disabled (or after its caps were
    # reached by this very run) must still receive the original run.
    existing = session.scalar(
        select(EvalRun).where(
            EvalRun.tenant_id == str(DEFAULT_TENANT_ID),
            EvalRun.created_by == created_by[:128],
            EvalRun.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.template_id != template.id:
            raise TemplateConflict(
                "幂等键已用于其他启动请求",
                details={"run_id": existing.id, "template_id": existing.template_id},
                code="idempotency_conflict",
            )
        return existing, False, _replay_launch_meta(template, existing)
    if service_token is not None:
        token_service.enforce_launch_caps(session, service_token)
    if not template.enabled:
        raise TemplateDisabled("模板已停用，不能启动")

    # Validate/decrypt the reporting target before resolving assets or
    # preparing: a broken target must never yield a partially launched run.
    self_evolution = _self_evolution_launch_payload(template)
    launch_asset: dict | None = None
    warnings: list[str] = []
    effective: dict = {}
    if asset_override is None:
        asset_snapshot_id = _resolve_template_assets(
            session, template, created_by=created_by, settings=settings
        )
    else:
        asset_snapshot_id, launch_asset, effective = _resolve_override_assets(
            session,
            template,
            asset_override,
            created_by=created_by,
            settings=settings,
        )
        warnings = _override_warnings(template)
    try:
        preparation = preparation_service.build_preparation(
            session,
            dataset_version_id=template.dataset_version_id,
            model_config_id=template.model_config_id,
            model_name=template.model_name,
            runtime_key=template.runtime_key,
            runtime_version=template.runtime_version,
            resource_pool_id=template.resource_pool_id,
            asset_snapshot_id=asset_snapshot_id,
            case_selection=template.case_selection_json or {"mode": "all"},
            execution=template.execution_json or {},
            prompt_suffix=template.prompt_suffix or None,
            max_turns=template.max_turns,
            agent_timeout_seconds=template.agent_timeout_seconds,
            self_evolution=self_evolution,
            created_by=created_by,
            settings=settings,
            now=now,
        )
    except preparation_service.PreparationError as error:
        raise _preparation_error(error) from error
    if not preparation.get("ready"):
        blockers = preparation.get("blockers") or []
        fields = {
            blocker["field"]: "invalid"
            for blocker in blockers
            if isinstance(blocker, dict) and blocker.get("field")
        }
        raise TemplateInvalid(
            "启动前一致性检查未通过",
            code="template_launch_blocked",
            details={
                "blockers": blockers,
                "warnings": preparation.get("warnings") or [],
                **({"fields": fields} if fields else {}),
            },
        )
    try:
        run, created = preparation_service.consume_preparation(
            session,
            preparation_id=preparation["preparation_id"],
            idempotency_key=key,
            created_by=created_by,
            name=name,
            settings=settings,
            now=now,
        )
    except preparation_service.PreparationError as error:
        raise _preparation_error(error) from error
    run.template_id = template.id
    run.template_version = template.version
    run.template_snapshot_json = template_view(session, template, settings=settings)
    if asset_override is not None:
        # Freeze both the raw request and the normalized identity so status,
        # results and a later replay echo what was actually measured.
        run.asset_override_json = {
            "requested": dict(asset_override),
            "effective": effective,
        }
    if lineage:
        # Caller provenance only: it never participates in evaluation
        # semantics, so it is stored verbatim and echoed by results.
        run.evolution_lineage_json = dict(lineage)
    template.launch_count = int(template.launch_count or 0) + 1
    template.last_launched_at = now
    session.commit()
    return run, created, {"asset": launch_asset, "warnings": warnings}
