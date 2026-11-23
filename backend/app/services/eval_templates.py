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
    AgentAssetSource,
    Dataset,
    DatasetVersion,
    EvalRun,
    EvalTemplate,
    ModelConfig,
    ResourcePool,
)
from ..money import money_str
from . import agent_assets as asset_service
from . import evaluations as evaluation_service
from . import preparations as preparation_service

TEMPLATE_STATUSES = ("enabled", "disabled")
TEMPLATE_SEARCH_LIMIT = 100


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


class TemplateInUse(TemplateError):
    status = 409
    code = "template_in_use"


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
    template = get_template(session, template_id)
    run_count = int(
        session.scalar(
            select(func.count())
            .select_from(EvalRun)
            .where(
                EvalRun.template_id == template.id,
                EvalRun.tenant_id == str(DEFAULT_TENANT_ID),
            )
        )
        or 0
    )
    if run_count:
        raise TemplateInUse(
            f"模板已有 {run_count} 个评测运行，不能删除；请改为停用",
            details={"run_count": run_count},
        )
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


def template_brief(session: Session, template_id: str | None) -> dict | None:
    if not template_id:
        return None
    template = session.scalar(
        select(EvalTemplate).where(
            EvalTemplate.id == template_id,
            EvalTemplate.tenant_id == str(DEFAULT_TENANT_ID),
        )
    )
    if template is None:
        return None
    return {
        "id": template.id,
        "name": template.name,
        "version": template.version,
        "enabled": bool(template.enabled),
    }


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


def launch_template(
    session: Session,
    template_id: str,
    *,
    idempotency_key: str,
    created_by: str,
    name: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[EvalRun, bool]:
    """Launch a template through the existing prepare/create path.

    Returns ``(run, created)``. A replayed idempotency key returns the original
    run of the same template; reusing a key for a different template is a 409.
    """

    settings = settings or get_settings()
    now = _now(now)
    key = (idempotency_key or "").strip()
    if not key:
        raise TemplateInvalid("启动必须提供幂等键", field="idempotency_key")
    key = key[:128]
    template = get_template(session, template_id)
    # Replay wins over the enabled check: a client that retries the same
    # idempotent launch after the template was disabled must still receive the
    # original run instead of a conflicting 409.
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
        return existing, False
    if not template.enabled:
        raise TemplateDisabled("模板已停用，不能启动")

    # Validate/decrypt the reporting target before resolving assets or
    # preparing: a broken target must never yield a partially launched run.
    self_evolution = _self_evolution_launch_payload(template)
    asset_snapshot_id = _resolve_template_assets(
        session, template, created_by=created_by, settings=settings
    )
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
    template.launch_count = int(template.launch_count or 0) + 1
    template.last_launched_at = now
    session.commit()
    return run, created
