"""Preparation consistency checks (M3-03, design 07 §5).

Blockers stop an evaluation from being created: unresolved datasets/refs,
unusable model credentials, unsupported runtimes or dataset environments,
missing execution/scoring images and runner label mismatches. Capacity
shortage is only a queue warning, never a hard failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..datasets import AdapterNotFound, get_adapter
from ..defaults import DEFAULT_TENANT_ID
from ..models import (
    Credential,
    Dataset,
    DatasetVersion,
    ModelConfig,
    ResourcePool,
    Runner,
)
from ..runtimes.base import AssetSnapshot
from ..runtimes.claude_code import get_runtime_adapter


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            item["field"] = self.field
        if self.details:
            item["details"] = self.details
        return item


@dataclass(frozen=True)
class PreflightResult:
    blockers: tuple[PreflightIssue, ...] = ()
    warnings: tuple[PreflightIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers

    def payload(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blockers": [issue.as_dict() for issue in self.blockers],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }


def _runner_labels(runner: Runner) -> set[str]:
    labels = runner.labels_json
    if isinstance(labels, dict):
        values = labels.get("labels", [])
        return set(values) if isinstance(values, list) else set()
    return set(labels) if isinstance(labels, list) else set()


def _model_name(config: ModelConfig) -> str:
    return (config.model_name or "").strip()


def check_preparation(
    session: Session,
    *,
    dataset_version_id: str,
    model_config_id: str,
    runtime_key: str = "claude-code",
    runtime_version: str | None = None,
    resource_pool_id: str,
    requested_concurrency: int = 1,
    scoring_image_digest: str | None = None,
    execution_image_digest: str | None = None,
    assets: AssetSnapshot | None = None,
    assets_resolved: bool = True,
    model_name: str | None = None,
) -> PreflightResult:
    tenant_id = str(DEFAULT_TENANT_ID)
    blockers: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []

    if requested_concurrency < 1:
        blockers.append(
            PreflightIssue("concurrency_invalid", "并发必须至少为 1", "requested_concurrency")
        )

    adapter = get_runtime_adapter(runtime_key)
    if adapter is None:
        blockers.append(
            PreflightIssue(
                "runtime_unsupported",
                f"不支持的 Runtime：{runtime_key}",
                "runtime_key",
            )
        )
        return PreflightResult(tuple(blockers), tuple(warnings))

    if runtime_version and not runtime_version.startswith(
        adapter.runtime_version.split(".")[0] + "."
    ):
        blockers.append(
            PreflightIssue(
                "runtime_version_unsupported",
                f"Runtime 版本 {runtime_version} 与适配器 {adapter.runtime_version} 不兼容",
                "runtime_version",
            )
        )

    version = session.get(DatasetVersion, dataset_version_id)
    if version is None or version.tenant_id != tenant_id:
        blockers.append(
            PreflightIssue(
                "dataset_version_not_found", "数据集版本不存在", "dataset_version_id"
            )
        )
    else:
        if version.status != "active":
            blockers.append(
                PreflightIssue("dataset_version_inactive", "数据集版本不可用", "dataset_version_id")
            )
        if version.case_count <= 0:
            blockers.append(
                PreflightIssue("dataset_version_empty", "数据集版本没有用例", "dataset_version_id")
            )
        if not (version.source_revision or "").strip():
            blockers.append(
                PreflightIssue("ref_unresolved", "数据集来源 Ref 尚未解析", "dataset_version_id")
            )
        dataset = session.get(Dataset, version.dataset_id)
        if dataset is None or dataset.tenant_id != tenant_id:
            blockers.append(
                PreflightIssue("dataset_not_found", "数据集不存在", "dataset_version_id")
            )
        else:
            if dataset.adapter_key not in adapter.supported_dataset_adapters:
                blockers.append(
                    PreflightIssue(
                        "capability_mismatch",
                        f"Runtime {runtime_key} 不支持数据集环境 {dataset.adapter_key}",
                        "dataset_version_id",
                        {"adapter_key": dataset.adapter_key},
                    )
                )
            try:
                get_adapter(dataset.adapter_key)
            except AdapterNotFound:
                blockers.append(
                    PreflightIssue(
                        "dataset_adapter_missing",
                        f"数据集适配器未注册：{dataset.adapter_key}",
                        "dataset_version_id",
                    )
                )

    config = session.get(ModelConfig, model_config_id)
    if config is None or config.tenant_id != tenant_id:
        blockers.append(PreflightIssue("model_config_missing", "模型配置不存在", "model_config_id"))
    else:
        configured_name = _model_name(config)
        if not configured_name:
            blockers.append(
                PreflightIssue("model_not_allowed", "模型配置缺少模型名", "model_config_id")
            )
        elif model_name and model_name != configured_name:
            blockers.append(
                PreflightIssue(
                    "model_not_allowed",
                    "所选模型名不在模型配置允许范围内",
                    "model_name",
                    {"allowed": [configured_name]},
                )
            )
        if not (config.base_url or "").startswith(("http://", "https://")):
            blockers.append(
                PreflightIssue("model_endpoint_invalid", "模型端点地址无效", "model_config_id")
            )
        credential = (
            session.get(Credential, config.credential_id) if config.credential_id else None
        )
        if (
            credential is None
            or credential.tenant_id != tenant_id
            or credential.revoked_at is not None
        ):
            blockers.append(
                PreflightIssue(
                    "credential_unavailable", "模型凭据不存在或已撤销", "model_config_id"
                )
            )

    pool = session.get(ResourcePool, resource_pool_id)
    if pool is None or pool.tenant_id != tenant_id:
        blockers.append(
            PreflightIssue("resource_pool_missing", "资源池不存在", "resource_pool_id")
        )
    elif pool.status != "active":
        blockers.append(
            PreflightIssue("resource_pool_inactive", "资源池不可用", "resource_pool_id")
        )
    else:
        runners = list(
            session.scalars(
                select(Runner).where(
                    Runner.tenant_id == tenant_id,
                    Runner.resource_pool_id == pool.id,
                    Runner.status.in_(("online", "busy")),
                )
            )
        )
        required = set(adapter.required_runner_labels)
        if runners:
            incompatible = [runner.name for runner in runners if not required <= _runner_labels(runner)]
            if incompatible:
                blockers.append(
                    PreflightIssue(
                        "runner_labels_mismatch",
                        "Runner 标签不满足 Runtime 要求",
                        "resource_pool_id",
                        {"required": sorted(required), "runners": incompatible},
                    )
                )
        online = sum(1 for runner in runners if runner.status == "online")
        if online == 0:
            warnings.append(
                PreflightIssue(
                    "capacity_queue_expected",
                    "当前没有空闲 Runner，评测将排队等待",
                    "resource_pool_id",
                )
            )
        elif requested_concurrency > online:
            warnings.append(
                PreflightIssue(
                    "capacity_queue_expected",
                    f"请求并发 {requested_concurrency} 超过空闲 Runner 数 {online}，将排队",
                    "requested_concurrency",
                    {"online": online},
                )
            )

    if not (scoring_image_digest or "").strip():
        blockers.append(
            PreflightIssue(
                "scoring_image_missing", "评分镜像 digest 缺失", "scoring_image_digest"
            )
        )
    if not (execution_image_digest or "").strip():
        blockers.append(
            PreflightIssue(
                "execution_image_missing", "执行镜像 digest 缺失", "execution_image_digest"
            )
        )

    if not assets_resolved:
        blockers.append(
            PreflightIssue("asset_unresolved", "Agent 资产快照尚未解析", "assets")
        )
    elif assets is not None:
        issues = adapter.validate_assets(assets)
        if issues:
            blockers.append(
                PreflightIssue(
                    "asset_invalid",
                    "Agent 资产快照校验失败",
                    "assets",
                    {"issues": [issue.__dict__ for issue in issues]},
                )
            )
    else:
        warnings.append(
            PreflightIssue("assets_empty", "未选择 Agent 资产，Runtime 将只使用内置能力", "assets")
        )

    return PreflightResult(tuple(blockers), tuple(warnings))
