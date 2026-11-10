"""Agent asset source scanning, immutable snapshots and OSS bundles (M5-01).

The flow is deliberately two-phase:

1. ``scan_repository`` reads a Git tree and identifies Skill directories
   (``SKILL.md``), Command and Subagent files without executing anything.
2. ``resolve_assets`` freezes the selected assets into a deterministic gzip
   JSON bundle in OSS, records per-file digests and returns an immutable
   snapshot row. Re-resolving the same commit + selection returns the existing
   snapshot (idempotent), so repeated Prepare calls never create a new version.

The eval run stores the snapshot metadata (commit, tree digest, bundle key and
asset list), never a branch name, so moving the branch after launch cannot
change a running evaluation.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..defaults import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from ..models import AgentAsset, AgentAssetSnapshot, AgentAssetSource, Credential, new_id
from ..runtimes.base import AssetSnapshot
from ..runtimes.claude_code import ClaudeCodeAdapter, get_runtime_adapter
from ..secrets import SecretBoxError, get_secret_box, mask_secret
from .git_client import GitHubClient, TreeEntry, parse_repo_url
from .object_store import ObjectStore, ObjectStoreError

ASSET_BUNDLE_SCHEMA = "1.0"
MAX_ASSET_FILES = 256
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_SCAN_UNCLASSIFIED = 200
SUPPORTED_RUNTIMES = ("claude-code",)


class AgentAssetError(Exception):
    """Stable asset-domain error surfaced as the shared API error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 422,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.field = field
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ScannedAsset:
    path: str
    asset_type: str
    name: str
    content_digest: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "asset_type": self.asset_type,
            "name": self.name,
            "content_digest": self.content_digest,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ScanResult:
    requested_ref: str
    resolved_commit: str
    assets: tuple[ScannedAsset, ...]
    unclassified: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    tree_entries: tuple[TreeEntry, ...] = ()
    blob_cache: dict[str, bytes] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        counts = asset_counts(self.assets)
        return {
            "resolved_commit": self.resolved_commit,
            "assets": [asset.as_dict() for asset in self.assets],
            "unclassified": list(self.unclassified),
            "counts": counts,
            "warnings": list(self.warnings),
        }


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _normalize_paths(paths: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        value = str(raw or "").strip().strip("/")
        if not value:
            continue
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
            raise AgentAssetError("scan_path_invalid", f"扫描路径无效：{raw}", field="scan_paths")
        normalized = str(candidate)
        if normalized not in seen:
            seen.add(normalized)
            cleaned.append(normalized)
    return cleaned


def _in_scope(path: str, scan_paths: list[str]) -> bool:
    if not scan_paths:
        return True
    return any(path == prefix or path.startswith(prefix + "/") for prefix in scan_paths)


def _under_any(path: str, directories: set[str]) -> bool:
    return any(path.startswith(directory + "/") for directory in directories)


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        normalized = key.strip()
        if normalized in ("name", "description"):
            values[normalized] = value.strip().strip("\"'")
    return values


def _decode_text(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


_RUNTIME_DIRS = (".claude", ".agents", ".codex", ".opencode", ".pi")


def _runtime_asset(path: str) -> tuple[str, str] | None:
    """Return ``(asset_type, runtime_path)`` for native runtime layouts only.

    A random ``src/commands`` folder is not a Claude Code command directory;
    unknown layouts stay unclassified so the operator must choose a type
    explicitly (design 02 §3.2 / 07 §4.1).
    """

    if not path.endswith(".md"):
        return None
    parts = PurePosixPath(path).parts
    for index in range(len(parts) - 2):
        if parts[index] in _RUNTIME_DIRS and parts[index + 1] in ("commands", "agents"):
            kind = "command" if parts[index + 1] == "commands" else "subagent"
            rest = parts[index + 2 :]
            if not rest:
                return None
            return kind, f"{parts[index + 1]}/" + "/".join(rest)
    if len(parts) >= 2 and parts[0] in ("commands", "agents"):
        kind = "command" if parts[0] == "commands" else "subagent"
        return kind, f"{parts[0]}/" + "/".join(parts[1:])
    return None


def _command_runtime_path(path: str) -> str:
    parts = PurePosixPath(path).parts
    if "commands" in parts[:-1]:
        index = parts.index("commands")
        return "commands/" + "/".join(parts[index + 1 :])
    return "commands/" + parts[-1]


def _subagent_runtime_path(path: str) -> str:
    return "agents/" + PurePosixPath(path).name


def _skill_name_from_path(path: str) -> str:
    return PurePosixPath(path).name


def asset_counts(assets: tuple[ScannedAsset, ...] | list[ScannedAsset]) -> dict[str, int]:
    counts = {"skill": 0, "command": 0, "subagent": 0}
    for asset in assets:
        if asset.asset_type in counts:
            counts[asset.asset_type] += 1
    return counts


def _credential_token(session: Session, credential_id: str) -> str:
    credential = session.get(Credential, credential_id)
    if credential is None or credential.tenant_id != str(DEFAULT_TENANT_ID):
        raise AgentAssetError("credential_not_found", "Git 凭据不存在", field="credential_id")
    try:
        return get_secret_box().decrypt(credential.ciphertext, credential.key_version)
    except SecretBoxError as error:
        raise AgentAssetError(
            "credential_unavailable", "Git 凭据解密失败", field="credential_id"
        ) from error


def _scan_payload(scan: ScanResult, requested_ref: str) -> dict[str, Any]:
    return {
        "requested_ref": requested_ref,
        "resolved_commit": scan.resolved_commit,
        "assets": [asset.as_dict() for asset in scan.assets],
        "unclassified": list(scan.unclassified),
        "counts": asset_counts(scan.assets),
        "warnings": list(scan.warnings),
        "scanned_at": datetime.now(UTC).isoformat(),
    }


def _client_token(session: Session, source: AgentAssetSource, settings: Settings) -> str | None:
    if source.credential_id:
        return _credential_token(session, source.credential_id)
    return settings.github_token


def preview_scan(
    session: Session,
    *,
    repo_url: str,
    requested_ref: str,
    scan_paths: list[str] | None = None,
    credential_id: str | None = None,
    access_token: str | None = None,
    settings: Settings | None = None,
) -> ScanResult:
    """Dry-run scan used by the add-source wizard before a source exists."""

    settings = settings or get_settings()
    token = (access_token or "").strip() or None
    if token is None and credential_id:
        token = _credential_token(session, credential_id)
    if token is None:
        token = settings.github_token
    client = GitHubClient(token=token, api_url=settings.github_api_url)
    try:
        return scan_repository(
            client=client,
            repo_url=repo_url,
            requested_ref=requested_ref,
            scan_paths=scan_paths,
        )
    finally:
        client.close()


def scan_repository(
    *,
    client: GitHubClient,
    repo_url: str,
    requested_ref: str,
    scan_paths: list[str] | None = None,
) -> ScanResult:
    """Resolve a Ref and discover assets without writing anything."""

    owner, repo = parse_repo_url(repo_url)
    ref = (requested_ref or "").strip()
    if not ref:
        raise AgentAssetError("ref_required", "Ref 不能为空", field="requested_ref")
    commit = client.resolve_commit(owner, repo, ref)
    entries = client.tree(owner, repo, commit)
    blobs = {entry.path: entry for entry in entries if entry.entry_type == "blob"}
    paths = _normalize_paths(scan_paths)
    scoped = [path for path in blobs if _in_scope(path, paths)]
    skill_dirs = {
        str(PurePosixPath(path).parent)
        for path in scoped
        if PurePosixPath(path).name == "SKILL.md"
    }
    blob_cache: dict[str, bytes] = {}

    def read_blob(path: str) -> bytes:
        entry = blobs[path]
        cached = blob_cache.get(entry.sha)
        if cached is None:
            cached = client.blob(owner, repo, entry.sha)
            blob_cache[entry.sha] = cached
        return cached

    assets: list[ScannedAsset] = []
    for directory in sorted(skill_dirs):
        manifest_path = f"{directory}/SKILL.md"
        content = read_blob(manifest_path)
        front = _frontmatter(_decode_text(content))
        # The directory name is the stable runtime path; frontmatter "name" is
        # display metadata only, so spaces/slashes cannot break the mapping.
        name = _skill_name_from_path(directory)
        owned = [
            path
            for path in blobs
            if path == directory or path.startswith(directory + "/")
        ]
        total_size = sum(blobs[path].size or 0 for path in owned)
        assets.append(
            ScannedAsset(
                path=directory,
                asset_type="skill",
                name=name,
                content_digest=_digest(content),
                metadata={
                    "description": front.get("description") or "",
                    "display_name": front.get("name") or name,
                    "files": len(owned),
                    "size": total_size,
                    "runtime_path": f"skills/{name}",
                    "prerequisites": [],
                },
            )
        )

    for path in sorted(scoped):
        if _under_any(path, skill_dirs):
            continue
        entry = blobs[path]
        runtime_asset = _runtime_asset(path)
        if runtime_asset is not None:
            asset_type, runtime_path = runtime_asset
            content = read_blob(path)
            name = runtime_path.split("/", 1)[1].removesuffix(".md")
            prerequisites = (
                ["openspec CLI"]
                if "openspec" in path.lower() or "openspec" in _decode_text(content).lower()
                else []
            )
            assets.append(
                ScannedAsset(
                    path=path,
                    asset_type=asset_type,
                    name=name,
                    content_digest=_digest(content),
                    metadata={
                        "description": _command_description(content),
                        "size": len(content),
                        "runtime_path": runtime_path,
                        "prerequisites": prerequisites if asset_type == "command" else [],
                    },
                )
            )

    classified = {asset.path for asset in assets}
    unclassified: list[dict[str, Any]] = []
    for path in sorted(scoped):
        if path in classified or _under_any(path, skill_dirs):
            continue
        if PurePosixPath(path).suffix.lower() not in (".md", ".toml", ".yaml", ".yml"):
            continue
        if len(unclassified) >= MAX_SCAN_UNCLASSIFIED:
            break
        entry = blobs[path]
        unclassified.append({"path": path, "size": entry.size or 0})

    warnings: list[str] = []
    counts = asset_counts(assets)
    if not assets:
        warnings.append("未发现可识别的 Skill、Command 或 Subagent")
    if unclassified:
        warnings.append(f"{len(unclassified)} 个文件无法自动识别类型，需要手动指定")
    if counts["command"] or counts["subagent"]:
        warnings.append("Command 与 Subagent 由所选 Runtime adapter 映射，跨 Runtime 不保证格式一致")
    return ScanResult(
        requested_ref=ref,
        resolved_commit=commit,
        assets=tuple(assets),
        unclassified=tuple(unclassified),
        warnings=tuple(warnings),
        tree_entries=tuple(entries),
        blob_cache=blob_cache,
    )


def _command_description(content: bytes) -> str:
    for line in _decode_text(content).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:300]
        if not stripped.startswith("---"):
            return stripped[:300]
    return ""


# ---------------------------------------------------------------------------
# Source CRUD helpers
# ---------------------------------------------------------------------------


def create_source(
    session: Session,
    *,
    name: str,
    repo_url: str,
    default_ref: str,
    scan_paths: list[str] | None = None,
    selection_mode: str = "directory",
    description: str | None = None,
    credential_id: str | None = None,
    access_token: str | None = None,
    runtime_prerequisites: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> AgentAssetSource:
    tenant_id = str(DEFAULT_TENANT_ID)
    project_id = str(DEFAULT_PROJECT_ID)
    settings = settings or get_settings()
    name = (name or "").strip()
    if not name:
        raise AgentAssetError("source_name_required", "资产源名称不能为空", field="name")
    default_ref = (default_ref or "").strip()
    if not default_ref:
        raise AgentAssetError("ref_required", "默认 Ref 不能为空", field="default_ref")
    # Reject non-GitHub URLs before persisting a source that can never scan.
    parse_repo_url(repo_url)
    if selection_mode not in ("directory", "explicit"):
        raise AgentAssetError(
            "selection_mode_invalid", "选择方式必须是 directory 或 explicit", field="selection_mode"
        )
    scan_paths = _normalize_paths(scan_paths)
    existing = session.scalar(
        select(AgentAssetSource).where(
            AgentAssetSource.tenant_id == tenant_id,
            AgentAssetSource.name == name,
        )
    )
    if existing is not None:
        raise AgentAssetError("source_name_conflict", "同名资产源已存在", status=409, field="name")
    if access_token and credential_id:
        raise AgentAssetError(
            "credential_conflict", "凭据只能选择已有凭据或填写新令牌", field="credential_id"
        )
    if credential_id:
        credential = session.get(Credential, credential_id)
        if credential is None or credential.tenant_id != tenant_id:
            raise AgentAssetError("credential_not_found", "Git 凭据不存在", field="credential_id")
    if access_token:
        token = access_token.strip()
        if not token:
            raise AgentAssetError("credential_invalid", "Git 访问令牌不能为空", field="access_token")
        ciphertext, key_version = get_secret_box().encrypt(token)
        credential = Credential(
            tenant_id=tenant_id,
            project_id=project_id,
            kind="resource",
            provider="github",
            ciphertext=ciphertext,
            key_version=key_version,
            secret_mask=mask_secret(token),
        )
        session.add(credential)
        session.flush()
        credential_id = credential.id
    source = AgentAssetSource(
        tenant_id=tenant_id,
        project_id=project_id,
        name=name,
        description=(description or "").strip() or None,
        repo_url=repo_url.strip(),
        provider="github",
        default_ref=default_ref,
        scan_paths_json=scan_paths,
        selection_mode=selection_mode,
        runtime_prerequisites_json=runtime_prerequisites or {},
        credential_id=credential_id,
        status="unverified",
        last_scan_json={},
    )
    session.add(source)
    session.commit()
    return source


def list_sources(session: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[AgentAssetSource], int]:
    tenant_id = str(DEFAULT_TENANT_ID)
    rows = list(
        session.scalars(
            select(AgentAssetSource)
            .where(AgentAssetSource.tenant_id == tenant_id)
            .order_by(AgentAssetSource.created_at.desc(), AgentAssetSource.id)
            .limit(limit)
            .offset(offset)
        )
    )
    total = session.scalar(
        select(func.count()).select_from(AgentAssetSource).where(
            AgentAssetSource.tenant_id == tenant_id
        )
    )
    return rows, int(total or 0)


def get_source(session: Session, source_id: str) -> AgentAssetSource:
    source = session.get(AgentAssetSource, source_id)
    if source is None or source.tenant_id != str(DEFAULT_TENANT_ID):
        raise AgentAssetError("asset_source_not_found", "资产源不存在", status=404)
    return source


def get_snapshot(session: Session, snapshot_id: str) -> AgentAssetSnapshot:
    snapshot = session.get(AgentAssetSnapshot, snapshot_id)
    if snapshot is None or snapshot.tenant_id != str(DEFAULT_TENANT_ID):
        raise AgentAssetError("asset_snapshot_not_found", "资产快照不存在", status=404)
    return snapshot


def snapshot_assets(session: Session, snapshot_id: str) -> list[AgentAsset]:
    return list(
        session.scalars(
            select(AgentAsset)
            .where(AgentAsset.snapshot_id == snapshot_id)
            .order_by(AgentAsset.ordinal, AgentAsset.asset_type, AgentAsset.path)
        )
    )


def scan_source(
    session: Session,
    source_id: str,
    *,
    requested_ref: str | None = None,
    client: GitHubClient | None = None,
    settings: Settings | None = None,
) -> ScanResult:
    settings = settings or get_settings()
    source = get_source(session, source_id)
    ref = (requested_ref or source.default_ref).strip()
    owns_client = client is None
    if client is None:
        client = GitHubClient(
            token=_client_token(session, source, settings),
            api_url=settings.github_api_url,
        )
    try:
        result = scan_repository(
            client=client,
            repo_url=source.repo_url,
            requested_ref=ref,
            scan_paths=list(source.scan_paths_json or []),
        )
    finally:
        if owns_client:
            client.close()
    source.last_scanned_commit = result.resolved_commit
    source.status = "ready" if result.assets else "requires_setup"
    source.last_scan_json = _scan_payload(result, ref)
    session.commit()
    return result


# ---------------------------------------------------------------------------
# Resolve + snapshot packaging
# ---------------------------------------------------------------------------


def _selection_digest(commit: str, runtime_key: str, assets: list[ScannedAsset]) -> str:
    payload = json.dumps(
        {
            "commit": commit,
            "runtime": runtime_key,
            "assets": sorted([asset.asset_type, asset.path] for asset in assets),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_runtime(runtime_key: str) -> None:
    adapter = get_runtime_adapter(runtime_key)
    if adapter is None:
        raise AgentAssetError(
            "runtime_assets_unsupported",
            f"Runtime {runtime_key} 不支持 Agent 资产注入",
            field="runtime_key",
        )


def _select_assets(
    *,
    scan: ScanResult,
    blobs: dict[str, Any],
    paths: list[str],
    types: dict[str, str] | None,
) -> list[ScannedAsset]:
    types = types or {}
    selected: list[ScannedAsset] = []
    by_path = {asset.path: asset for asset in scan.assets}
    seen: set[tuple[str, str]] = set()

    def add(asset: ScannedAsset) -> None:
        key = (asset.asset_type, asset.path)
        if key not in seen:
            seen.add(key)
            selected.append(asset)

    for raw in paths:
        path = str(raw or "").strip().strip("/")
        if not path:
            continue
        if path in by_path:
            add(by_path[path])
            continue
        prefix = path + "/"
        prefixed = [asset for asset in scan.assets if asset.path.startswith(prefix)]
        if prefixed:
            for asset in prefixed:
                add(asset)
            continue
        explicit = types.get(path)
        if explicit is None:
            raise AgentAssetError(
                "asset_type_unresolved",
                f"无法判断资产类型：{path}；请在请求中显式指定 asset_type",
                details={"path": path},
            )
        if explicit not in ("skill", "command", "subagent"):
            raise AgentAssetError("asset_type_invalid", f"未知资产类型：{explicit}", details={"path": path})
        if explicit == "skill":
            manifest = f"{path}/SKILL.md"
            if manifest not in blobs:
                raise AgentAssetError(
                    "skill_manifest_missing", f"Skill 目录缺少 SKILL.md：{path}", details={"path": path}
                )
            owned = [candidate for candidate in blobs if candidate == path or candidate.startswith(prefix)]
            if not owned:
                raise AgentAssetError("asset_path_not_found", f"资产路径不存在：{path}", details={"path": path})
            add(
                ScannedAsset(
                    path=path,
                    asset_type="skill",
                    name=_skill_name_from_path(path),
                    content_digest="",
                    metadata={
                        "files": len(owned),
                        "size": sum(blobs[candidate].size or 0 for candidate in owned),
                        "runtime_path": f"skills/{_skill_name_from_path(path)}",
                        "prerequisites": [],
                        "explicit": True,
                    },
                )
            )
            continue
        if path not in blobs:
            raise AgentAssetError("asset_path_not_found", f"资产路径不存在：{path}", details={"path": path})
        if not path.endswith(".md"):
            raise AgentAssetError(
                "asset_extension_invalid", f"{explicit} 资产必须是 .md 文件：{path}", details={"path": path}
            )
        runtime_path = (
            _command_runtime_path(path) if explicit == "command" else _subagent_runtime_path(path)
        )
        add(
            ScannedAsset(
                path=path,
                asset_type=explicit,
                name=(
                    runtime_path.removeprefix("commands/").removesuffix(".md")
                    if explicit == "command"
                    else PurePosixPath(path).stem
                ),
                content_digest="",
                metadata={
                    "runtime_path": runtime_path,
                    "size": blobs[path].size or 0,
                    "prerequisites": ["openspec CLI"] if "openspec" in path.lower() else [],
                    "explicit": True,
                },
            )
        )
    if not selected:
        raise AgentAssetError("asset_selection_empty", "至少选择一个 Agent 资产", field="paths")
    return selected


def _pack_assets(
    *,
    source: AgentAssetSource,
    commit: str,
    scan: ScanResult,
    blobs: dict[str, Any],
    selected: list[ScannedAsset],
    runtime_key: str,
    settings: Settings,
    client: GitHubClient,
    modes: dict[str, str],
) -> tuple[dict[str, bytes], list[dict[str, Any]], str]:
    owner, repo = parse_repo_url(source.repo_url)
    planned: set[str] = set()
    for asset in selected:
        if asset.asset_type == "skill":
            planned.update(
                path
                for path in blobs
                if path == asset.path or path.startswith(asset.path + "/")
            )
        else:
            planned.add(asset.path)
    # Bound before downloading: a large repository file must fail cheaply
    # instead of being fetched into memory and rejected afterwards.
    if len(planned) > MAX_ASSET_FILES:
        raise AgentAssetError(
            "asset_count_exceeded",
            f"资产文件数 {len(planned)} 超过上限 {MAX_ASSET_FILES}",
            details={"count": len(planned)},
        )
    planned_bytes = sum(blobs[path].size or 0 for path in planned)
    if planned_bytes > MAX_ASSET_BYTES:
        raise AgentAssetError(
            "asset_size_exceeded",
            f"资产总大小 {planned_bytes} 超过上限 {MAX_ASSET_BYTES}",
            details={"bytes": planned_bytes},
        )
    files: dict[str, bytes] = {}
    assets_payload: list[dict[str, Any]] = []
    total_bytes = 0

    def read(path: str) -> bytes:
        entry = blobs[path]
        cached = scan.blob_cache.get(entry.sha)
        if cached is None:
            cached = client.blob(owner, repo, entry.sha)
            scan.blob_cache[entry.sha] = cached
        return cached

    for asset in sorted(selected, key=lambda item: (item.asset_type, item.path)):
        asset_files: list[dict[str, Any]] = []
        if asset.asset_type == "skill":
            owned = sorted(
                path
                for path in blobs
                if path == asset.path or path.startswith(asset.path + "/")
            )
            for path in owned:
                relative = path[len(asset.path) + 1 :]
                destination = f"skills/{asset.name}/{relative}"
                content = read(path)
                if destination in files:
                    raise AgentAssetError(
                        "asset_path_conflict",
                        f"资产文件映射冲突：{destination}",
                        details={"path": destination},
                    )
                files[destination] = content
                total_bytes += len(content)
                asset_files.append(
                    {
                        "path": destination,
                        "sha256": _digest(content),
                        "size": len(content),
                        "mode": modes.get(path, "100644"),
                    }
                )
        else:
            destination = str(asset.metadata["runtime_path"])
            content = read(asset.path)
            if destination in files:
                raise AgentAssetError(
                    "asset_path_conflict", f"资产文件映射冲突：{destination}", details={"path": destination}
                )
            files[destination] = content
            total_bytes += len(content)
            asset_files.append(
                {
                    "path": destination,
                    "sha256": _digest(content),
                    "size": len(content),
                    "mode": modes.get(asset.path, "100644"),
                }
            )
        if not asset_files:
            raise AgentAssetError("asset_empty", f"资产没有文件：{asset.path}", details={"path": asset.path})
        content_digest = _digest(
            "\n".join(f"{item['path']}\0{item['sha256']}" for item in asset_files).encode("utf-8")
        )
        assets_payload.append(
            {
                **asset.as_dict(),
                "content_digest": content_digest,
                "files": asset_files,
                "metadata": {
                    **asset.metadata,
                    "file_count": len(asset_files),
                    "size": sum(item["size"] for item in asset_files),
                },
            }
        )
    if len(files) > MAX_ASSET_FILES:
        raise AgentAssetError(
            "asset_count_exceeded",
            f"资产文件数 {len(files)} 超过上限 {MAX_ASSET_FILES}",
            details={"count": len(files)},
        )
    if total_bytes > MAX_ASSET_BYTES:
        raise AgentAssetError(
            "asset_size_exceeded",
            f"资产总大小 {total_bytes} 超过上限 {MAX_ASSET_BYTES}",
            details={"bytes": total_bytes},
        )
    issues = ClaudeCodeAdapter().validate_assets(AssetSnapshot(files=files))
    if issues:
        raise AgentAssetError(
            "asset_invalid",
            "Agent 资产快照校验失败",
            details={"issues": [issue.__dict__ for issue in issues]},
        )
    tree_digest = _digest(
        "\n".join(
            f"{path}\0{_digest(files[path])}" for path in sorted(files)
        ).encode("utf-8")
    )
    return files, assets_payload, tree_digest


def _bundle_key(tenant_id: str, snapshot_id: str) -> str:
    return f"agent-assets/{tenant_id}/{snapshot_id}/bundle.json.gz"


def _object_store(settings: Settings) -> ObjectStore:
    if not (settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key):
        raise AgentAssetError(
            "object_store_unavailable",
            "对象存储未配置，无法冻结资产快照",
            status=503,
            field="asset_snapshot",
        )
    return ObjectStore(
        endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        addressing_style=settings.s3_addressing_style,
    )


def resolve_assets(
    session: Session,
    *,
    source_id: str,
    requested_ref: str | None,
    paths: list[str],
    types: dict[str, str] | None = None,
    runtime_key: str = "claude-code",
    created_by: str,
    client: GitHubClient | None = None,
    object_store: ObjectStore | None = None,
    settings: Settings | None = None,
) -> tuple[AgentAssetSnapshot, list[AgentAsset], bool]:
    """Freeze the selected assets; return ``(snapshot, assets, created)``."""

    settings = settings or get_settings()
    _validate_runtime(runtime_key)
    source = get_source(session, source_id)
    ref = (requested_ref or source.default_ref).strip()
    if not ref:
        raise AgentAssetError("ref_required", "评测 Ref 不能为空", field="requested_ref")
    owns_client = client is None
    if client is None:
        client = GitHubClient(
            token=_client_token(session, source, settings),
            api_url=settings.github_api_url,
        )
    try:
        owner, repo = parse_repo_url(source.repo_url)
        scan = scan_repository(
            client=client,
            repo_url=source.repo_url,
            requested_ref=ref,
            scan_paths=list(source.scan_paths_json or []),
        )
        commit = scan.resolved_commit
        blobs = {entry.path: entry for entry in scan.tree_entries if entry.entry_type == "blob"}
        selected = _select_assets(scan=scan, blobs=blobs, paths=paths, types=types)
        digest = _selection_digest(commit, runtime_key, selected)
        existing = session.scalar(
            select(AgentAssetSnapshot).where(
                AgentAssetSnapshot.tenant_id == str(DEFAULT_TENANT_ID),
                AgentAssetSnapshot.source_id == source.id,
                AgentAssetSnapshot.selection_digest == digest,
            )
        )
        if existing is not None:
            # A repeated resolve is still a successful scan: keep the source
            # view consistent even when no new bundle is written.
            source.last_scanned_commit = commit
            source.status = "ready" if scan.assets else "requires_setup"
            source.last_scan_json = _scan_payload(scan, ref)
            session.commit()
            return existing, snapshot_assets(session, existing.id), False

        snapshot_id = new_id()
        modes = {entry.path: entry.mode for entry in scan.tree_entries}
        files, assets_payload, tree_digest = _pack_assets(
            source=source,
            commit=commit,
            scan=scan,
            blobs=blobs,
            selected=selected,
            runtime_key=runtime_key,
            settings=settings,
            client=client,
            modes=modes,
        )
        bundle_doc = {
            "schema_version": ASSET_BUNDLE_SCHEMA,
            "snapshot_id": snapshot_id,
            "resolved_commit": commit,
            "tree_digest": tree_digest,
            "files": [
                {
                    "path": path,
                    "sha256": _digest(files[path]),
                    "size": len(files[path]),
                    "mode": modes.get(path, "100644"),
                    "content_base64": base64.b64encode(files[path]).decode("ascii"),
                }
                for path in sorted(files)
            ],
        }
        raw = json.dumps(bundle_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        bundle = gzip.compress(raw, compresslevel=9, mtime=0)
        bundle_digest = _digest(bundle)
        key = _bundle_key(str(DEFAULT_TENANT_ID), snapshot_id)
        store = object_store or _object_store(settings)
        try:
            store.put_bytes(key, bundle, content_type="application/gzip")
        except ObjectStoreError as error:
            raise AgentAssetError(
                "object_store_unavailable",
                f"资产包上传失败：{error}",
                status=503,
                field="asset_snapshot",
            ) from error

        manifest = {
            "schema_version": ASSET_BUNDLE_SCHEMA,
            "files": [
                {
                    "path": path,
                    "sha256": _digest(files[path]),
                    "size": len(files[path]),
                    "mode": modes.get(path, "100644"),
                }
                for path in sorted(files)
            ],
            "assets": assets_payload,
            "validation": {
                "runtime_key": runtime_key,
                "adapter_version": ClaudeCodeAdapter.adapter_version,
                "ok": True,
            },
            "tree_digest": tree_digest,
            "resolved_commit": commit,
        }
        snapshot = AgentAssetSnapshot(
            id=snapshot_id,
            tenant_id=str(DEFAULT_TENANT_ID),
            source_id=source.id,
            requested_ref=ref,
            resolved_commit=commit,
            selection_mode="explicit",
            scan_paths_json=list(source.scan_paths_json or []),
            selection_json=[
                {"asset_type": asset.asset_type, "path": asset.path, "name": asset.name}
                for asset in sorted(selected, key=lambda item: (item.asset_type, item.path))
            ],
            selection_digest=digest,
            tree_digest=tree_digest,
            bundle_key=key,
            bundle_digest=bundle_digest,
            bundle_size=len(bundle),
            file_count=len(files),
            asset_count=len(selected),
            runtime_key=runtime_key,
            manifest_json=manifest,
            created_by=created_by[:128],
        )
        session.add(snapshot)
        # Explicit flush: the composite FK (tenant_id, snapshot_id) is not
        # backed by a relationship, so insert the parent before its children.
        session.flush()
        for index, asset in enumerate(assets_payload):
            session.add(
                AgentAsset(
                    tenant_id=str(DEFAULT_TENANT_ID),
                    snapshot_id=snapshot_id,
                    asset_type=asset["asset_type"],
                    name=asset["name"],
                    path=asset["path"],
                    content_digest=asset["content_digest"],
                    metadata_json={
                        **asset["metadata"],
                        "description": asset["metadata"].get("description", ""),
                        "files": asset["files"],
                    },
                    ordinal=index,
                )
            )
        source.last_scanned_commit = commit
        source.status = "ready" if scan.assets else "requires_setup"
        source.last_scan_json = _scan_payload(scan, ref)
        try:
            session.commit()
        except IntegrityError:
            # A parallel resolve won the unique(tenant, source, selection)
            # race; return the winner instead of a duplicate bundle row.
            session.rollback()
            winner = session.scalar(
                select(AgentAssetSnapshot).where(
                    AgentAssetSnapshot.tenant_id == str(DEFAULT_TENANT_ID),
                    AgentAssetSnapshot.source_id == source.id,
                    AgentAssetSnapshot.selection_digest == digest,
                )
            )
            if winner is None:
                raise
            return winner, snapshot_assets(session, winner.id), False
        session.refresh(snapshot)
        return snapshot, snapshot_assets(session, snapshot.id), True
    finally:
        if owns_client:
            client.close()


# ---------------------------------------------------------------------------
# Serialization for the console / eval run
# ---------------------------------------------------------------------------


def asset_dict(asset: AgentAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "snapshot_id": asset.snapshot_id,
        "asset_type": asset.asset_type,
        "name": asset.name,
        "path": asset.path,
        "content_digest": asset.content_digest,
        "metadata": asset.metadata_json,
        "ordinal": asset.ordinal,
    }


def snapshot_dict(snapshot: AgentAssetSnapshot, assets: list[AgentAsset]) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "source_id": snapshot.source_id,
        "requested_ref": snapshot.requested_ref,
        "resolved_commit": snapshot.resolved_commit,
        "selection_mode": snapshot.selection_mode,
        "selection": snapshot.selection_json,
        "tree_digest": snapshot.tree_digest,
        "bundle_key": snapshot.bundle_key,
        "bundle_digest": snapshot.bundle_digest,
        "bundle_size": snapshot.bundle_size,
        "file_count": snapshot.file_count,
        "asset_count": snapshot.asset_count,
        "runtime_key": snapshot.runtime_key,
        "manifest": snapshot.manifest_json,
        "created_by": snapshot.created_by,
        "created_at": snapshot.created_at.isoformat(timespec="microseconds"),
        "assets": [asset_dict(asset) for asset in assets],
    }


def asset_snapshot_json(snapshot: AgentAssetSnapshot, assets: list[AgentAsset]) -> dict[str, Any]:
    """The frozen part stored on eval_run (no presigned URL)."""

    return {
        "snapshot_id": snapshot.id,
        "source_id": snapshot.source_id,
        "requested_ref": snapshot.requested_ref,
        "resolved_commit": snapshot.resolved_commit,
        "tree_digest": snapshot.tree_digest,
        "bundle_key": snapshot.bundle_key,
        "bundle_digest": snapshot.bundle_digest,
        "bundle_size": snapshot.bundle_size,
        "file_count": snapshot.file_count,
        "asset_count": snapshot.asset_count,
        "runtime_key": snapshot.runtime_key,
        "assets": [
            {
                "asset_type": asset.asset_type,
                "name": asset.name,
                "path": asset.path,
                "content_digest": asset.content_digest,
                "metadata": asset.metadata_json,
            }
            for asset in assets
        ],
    }


def source_dict(
    source: AgentAssetSource,
    *,
    credential_mask: str | None = None,
    uses_platform_token: bool = False,
) -> dict[str, Any]:
    scan = source.last_scan_json or {}
    return {
        "id": source.id,
        "name": source.name,
        "description": source.description,
        "repo_url": source.repo_url,
        "provider": source.provider,
        "default_ref": source.default_ref,
        "scan_paths": list(source.scan_paths_json or []),
        "selection_mode": source.selection_mode,
        "status": source.status,
        "runtime_prerequisites": source.runtime_prerequisites_json or {},
        "last_scanned_commit": source.last_scanned_commit,
        "last_scan": scan,
        "counts": scan.get("counts", {"skill": 0, "command": 0, "subagent": 0}),
        "credential_mask": credential_mask,
        "uses_platform_token": uses_platform_token,
        "created_at": source.created_at.isoformat(timespec="microseconds"),
        "updated_at": source.updated_at.isoformat(timespec="microseconds"),
    }


def source_credential_mask(session: Session, source: AgentAssetSource) -> str | None:
    if not source.credential_id:
        return None
    credential = session.get(Credential, source.credential_id)
    return credential.secret_mask if credential is not None else None


def task_asset_payload(asset_snapshot: dict[str, Any] | None, settings: Settings) -> dict[str, Any]:
    """Add a short-lived presigned bundle URL to the frozen snapshot metadata."""

    payload = dict(asset_snapshot or {})
    key = payload.get("bundle_key")
    if not key:
        return payload
    try:
        store = _object_store(settings)
        payload["bundle_url"] = store.presign_get_url(str(key), expires=3600)
    except (AgentAssetError, ObjectStoreError):
        payload["bundle_error"] = "object_store_unavailable"
    return payload
