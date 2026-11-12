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
import os
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
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

# M8-10: runtime prerequisites are administrator-configured shell commands.
# The load test executes them only in a fresh temporary HOME and keeps a
# bounded log/asset footprint so a runaway CLI cannot grow the database.
LOAD_TEST_TIMEOUT_SECONDS = 600
LOAD_TEST_TOKEN_TTL_SECONDS = 600
MAX_RUNTIME_COMMAND_LENGTH = 2000
MAX_RUNTIME_LABEL_LENGTH = 100
MAX_LOAD_TEST_LOG_TAIL = 20_000
RUNTIME_PREREQUISITE_KEYS = ("install_command", "init_command", "runtime")
DEFAULT_SHELL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Preview tokens are intentionally process-local: they only bridge the add
# wizard's "test" click to the following create request. A multi-replica API
# may miss the cache on another instance, which falls back to requires_setup
# instead of trusting client-provided generated assets.
_LOAD_TEST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOAD_TEST_CACHE_LOCK = threading.Lock()



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
        # "." explicitly means the repository root; the empty list already
        # means "scan everything", so normalize it rather than scope to "./".
        if not value or value == ".":
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
        if normalized in ("name", "description", "compatibility"):
            values[normalized] = value.strip().strip("\"'")
    return values


def _decode_text(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


_REQUIRES_CLI = re.compile(r"requires?\s+([A-Za-z0-9_.-]+)\s+cli\b", re.IGNORECASE)


def _runtime_prerequisites(*, path: str, front: dict[str, str], content: str) -> list[str]:
    """Return the CLI dependencies an asset declares or clearly references.

    The SKILL.md frontmatter is the authoritative source: an OpenSpec skill
    carries ``compatibility: Requires openspec CLI.``. Content/path keywords
    are the fallback for repositories that omit that field (for example the
    generated ``opsx/*`` commands). No prerequisites are invented when the
    manifest says nothing and the body does not mention a known CLI.
    """

    found: list[str] = []
    match = _REQUIRES_CLI.search(front.get("compatibility", ""))
    if match:
        found.append(f"{match.group(1)} CLI")
    lowered = f"{path}\n{content}".lower()
    if "openspec" in lowered and "openspec CLI" not in found:
        found.append("openspec CLI")
    return found


def _scan_status(assets: tuple[ScannedAsset, ...] | list[ScannedAsset]) -> str:
    """Map scanned assets onto the persisted source status.

    ``requires_setup`` means the assets exist but need runtime preparation
    (CLI and/or generated commands); an empty scan also stays
    ``requires_setup`` so the operator has to act instead of seeing a ready
    source with no assets.
    """

    if not assets:
        return "requires_setup"
    return "requires_setup" if any(asset.metadata.get("prerequisites") for asset in assets) else "ready"


def normalize_runtime_prerequisites(
    value: dict[str, Any] | None, *, field: str = "runtime_prerequisites"
) -> dict[str, str]:
    """Validate the optional runtime-prerequisite commands without semantics.

    The platform never interprets these strings; it only requires a non-empty
    install and init command pair so the load test can run them safely.
    """

    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        raise AgentAssetError(
            "runtime_prerequisites_invalid", "运行时前置必须是对象", field=field
        )
    unknown = sorted(set(value) - set(RUNTIME_PREREQUISITE_KEYS))
    if unknown:
        raise AgentAssetError(
            "runtime_prerequisites_invalid",
            "运行时前置包含不支持的字段：" + ", ".join(unknown),
            field=field,
        )
    normalized: dict[str, str] = {}
    limits = {
        "install_command": MAX_RUNTIME_COMMAND_LENGTH,
        "init_command": MAX_RUNTIME_COMMAND_LENGTH,
        "runtime": MAX_RUNTIME_LABEL_LENGTH,
    }
    for key in RUNTIME_PREREQUISITE_KEYS:
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise AgentAssetError(
                "runtime_prerequisites_invalid", f"运行时前置 {key} 必须是字符串", field=field
            )
        text = raw.strip()
        if not text:
            raise AgentAssetError(
                "runtime_prerequisites_invalid", f"运行时前置 {key} 不能为空", field=field
            )
        if len(text) > limits[key]:
            raise AgentAssetError(
                "runtime_prerequisites_invalid",
                f"运行时前置 {key} 超过 {limits[key]} 个字符",
                field=field,
            )
        normalized[key] = text
    if not normalized:
        return {}
    if "install_command" not in normalized or "init_command" not in normalized:
        raise AgentAssetError(
            "runtime_prerequisites_incomplete",
            "运行时前置需要同时提供安装命令与初始化命令",
            field=field,
        )
    return normalized


def _has_prerequisites(source: AgentAssetSource) -> bool:
    return bool(source.runtime_prerequisites_json or {})


def _load_test_passed(source: AgentAssetSource) -> bool:
    return (source.load_test_json or {}).get("status") == "passed"


def _source_status(
    source: AgentAssetSource, assets: tuple[ScannedAsset, ...] | list[ScannedAsset]
) -> str:
    """A source with configured prerequisites is only ready after a pass."""

    if _has_prerequisites(source):
        return "ready" if _load_test_passed(source) else "requires_setup"
    if _load_test_passed(source):
        return "ready"
    return _scan_status(assets)



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


# ---------------------------------------------------------------------------
# M8-10: isolated CLI load test and generated-asset scanning
# ---------------------------------------------------------------------------


def _decode_command_output(data: bytes | str | None) -> str:
    """Decode CLI output without ever failing the request.

    A CLI may print arbitrary bytes (progress bars, binary notices, a partially
    written multibyte character). ``subprocess`` in text mode decodes strictly
    with the locale codec and raises ``UnicodeDecodeError``; that is not a
    load-test outcome but a 500. Decode as UTF-8 with replacement instead.
    """

    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the shell and everything it spawned (npm spawns grandchildren)."""

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


def run_setup_command(
    command: str, *, cwd: str, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """Execute one administrator-configured prerequisite command.

    Kept as a tiny module-level helper so tests can replace the shell without
    invoking npm/network access. Production always uses a login shell in the
    isolated temporary HOME, started in its own session so a timeout can kill
    the whole process tree instead of orphan child processes holding pipes.

    Output is captured as bytes and decoded with ``errors="replace"`` so no CLI
    output can turn into an unhandled ``UnicodeDecodeError``.
    """

    process = subprocess.Popen(
        ["/bin/sh", "-lc", command],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        # Reap the shell after SIGKILL; a descendant that escaped the group can
        # still hold the pipes, so this second wait is bounded.
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = b"", b""
        raise subprocess.TimeoutExpired(
            command, timeout, output=stdout, stderr=stderr
        ) from None
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=_decode_command_output(stdout),
        stderr=_decode_command_output(stderr),
    )


def _log_tail(chunks: list[str]) -> str:
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    return text[-MAX_LOAD_TEST_LOG_TAIL:]


def _append_command_log(chunks: list[str], phase: str, command: str, output: str) -> None:
    body = (output or "").strip()
    if body:
        chunks.append(f"$ {phase}: {command}\n{body}")
    else:
        chunks.append(f"$ {phase}: {command}")


def _load_test_budget() -> dict[str, int]:
    return {"files": 0, "bytes": 0}


def _read_generated_file(path: Path, root: Path, budget: dict[str, int]) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise AgentAssetError(
            "generated_asset_invalid", f"生成的资产路径越界：{path}", status=422
        ) from error
    if path.is_symlink() or not path.is_file():
        raise AgentAssetError(
            "generated_asset_invalid", f"生成的资产不是普通文件：{path}", status=422
        )
    size = path.stat().st_size
    budget["files"] += 1
    budget["bytes"] += size
    if budget["files"] > MAX_ASSET_FILES or budget["bytes"] > MAX_ASSET_BYTES:
        raise AgentAssetError(
            "generated_assets_too_large",
            f"生成的资产超过上限（{MAX_ASSET_FILES} 个文件 / {MAX_ASSET_BYTES} 字节）",
            status=422,
        )
    data = path.read_bytes()
    return {
        "path": resolved.relative_to(root.resolve()).as_posix(),
        "content": _decode_text(data),
        "sha256": _digest(data),
        "size": len(data),
        "mode": "100644",
    }


def _walk_generated_files(
    directory: Path, root: Path, budget: dict[str, int], *, suffix: str | None = None
) -> list[dict[str, Any]]:
    """Read regular files under ``directory``; symlinks are never followed."""

    if not directory.is_dir() or directory.is_symlink():
        return []
    files: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames if not (current_path / name).is_symlink()
        )
        for name in sorted(filenames):
            path = current_path / name
            if path.is_symlink() or (suffix is not None and path.suffix != suffix):
                continue
            files.append(_read_generated_file(path, root, budget))
    return files


def _generated_asset(
    *,
    asset_type: str,
    name: str,
    path: str,
    content: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "asset_type": asset_type,
        "name": name,
        "path": path,
        "content": content,
        "files": files,
    }


def _scan_generated_assets(root: Path) -> list[dict[str, Any]]:
    """Find the ``.claude`` assets an install/init command generated.

    Skills own every file under ``.claude/skills/<name>/``; commands are
    recursive Markdown files; subagents are top-level Markdown files. Output is
    deterministic and bounded, and stays in the source's stored load-test JSON.
    """

    budget = _load_test_budget()
    generated: list[dict[str, Any]] = []
    claude = root / ".claude"

    skills_root = claude / "skills"
    if skills_root.is_dir() and not skills_root.is_symlink():
        for skill_dir in sorted(
            path
            for path in skills_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ):
            manifest = skill_dir / "SKILL.md"
            if not manifest.is_file() or manifest.is_symlink():
                continue
            files = _walk_generated_files(skill_dir, root, budget)
            manifest_file = next(
                (item for item in files if item["path"] == manifest.relative_to(root).as_posix()),
                None,
            )
            if manifest_file is None:
                continue
            generated.append(
                _generated_asset(
                    asset_type="skill",
                    name=skill_dir.name,
                    path=manifest.relative_to(root).parent.as_posix(),
                    content=manifest_file["content"],
                    files=files,
                )
            )

    commands_root = claude / "commands"
    for item in _walk_generated_files(commands_root, root, budget, suffix=".md"):
        relative = PurePosixPath(item["path"]).relative_to(".claude/commands")
        name = str(relative.with_suffix(""))
        generated.append(
            _generated_asset(
                asset_type="command",
                name=name,
                path=item["path"],
                content=item["content"],
                files=[item],
            )
        )

    agents_root = claude / "agents"
    if agents_root.is_dir() and not agents_root.is_symlink():
        for path in sorted(agents_root.iterdir()):
            if path.suffix != ".md" or path.is_symlink() or not path.is_file():
                continue
            item = _read_generated_file(path, root, budget)
            generated.append(
                _generated_asset(
                    asset_type="subagent",
                    name=path.stem,
                    path=item["path"],
                    content=item["content"],
                    files=[item],
                )
            )

    return sorted(generated, key=lambda item: (item["asset_type"], item["path"]))


def _load_test_counts(generated: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"skill": 0, "command": 0, "subagent": 0}
    for item in generated:
        asset_type = item.get("asset_type")
        if asset_type in counts:
            counts[asset_type] += 1
    return counts


def load_test_view(result: dict[str, Any]) -> dict[str, Any]:
    """The API-facing result: counts and paths, never generated file bodies."""

    if not result:
        return {}
    generated = result.get("generated") or []
    if isinstance(generated, dict):
        counts = {key: int(generated.get(key) or 0) for key in ("skill", "command", "subagent")}
        assets: list[dict[str, Any]] = []
    else:
        counts = _load_test_counts(generated)
        assets = [
            {
                "asset_type": item.get("asset_type"),
                "name": item.get("name"),
                "path": item.get("path"),
            }
            for item in sorted(
                generated, key=lambda entry: (entry.get("asset_type", ""), entry.get("path", ""))
            )
        ]
    view: dict[str, Any] = {
        "status": result.get("status"),
        "duration_seconds": result.get("duration_seconds"),
        "ran_at": result.get("ran_at"),
        "install_command": result.get("install_command"),
        "init_command": result.get("init_command"),
        "generated": counts,
        "assets": assets,
        "log_tail": result.get("log_tail") or "",
    }
    if result.get("runtime"):
        view["runtime"] = result["runtime"]
    if result.get("error"):
        view["error"] = result["error"]
    return view


def runtime_setup_view(manifest: dict[str, Any] | None) -> dict[str, str]:
    """The frozen install/init commands a runner must execute before the run.

    Only the snapshot manifest is consulted: a later edit of the source's
    prerequisites cannot change what a frozen asset snapshot requires. The
    load-test result is the fallback for snapshots written before the
    prerequisites were mirrored into the manifest.
    """

    manifest = manifest or {}
    prerequisites = manifest.get("runtime_prerequisites") or {}
    load_test = manifest.get("load_test") or {}
    install_command = str(
        prerequisites.get("install_command") or load_test.get("install_command") or ""
    ).strip()
    init_command = str(
        prerequisites.get("init_command") or load_test.get("init_command") or ""
    ).strip()
    if not install_command or not init_command:
        return {}
    return {
        "install_command": install_command,
        "init_command": init_command,
    }


def _execute_load_test(
    prerequisites: dict[str, str], *, runner=None
) -> tuple[dict[str, Any], bool]:
    """Run install then init in a temp HOME and scan the generated assets.

    Returns ``(stored_result, infrastructure_error)``. A non-zero exit or an
    empty generated tree is a normal failed test; a timeout/OSError is an
    infrastructure error surfaced as source.status=error.

    SECURITY: this is process-level isolation, not a sandbox. The commands
    are administrator-configured and run as the API worker user with the host
    filesystem and network visible; only HOME/TMPDIR state is disposable.
    Do not expose this endpoint to untrusted roles.
    """

    runner = runner or run_setup_command
    started = time.monotonic()
    chunks: list[str] = []
    generated: list[dict[str, Any]] = []
    status = "passed"
    error: str | None = None
    infrastructure_error = False
    install_command = prerequisites["install_command"]
    init_command = prerequisites["init_command"]

    with tempfile.TemporaryDirectory(prefix="agent-asset-load-test-") as workdir:
        env = {
            "HOME": workdir,
            "PATH": os.environ.get("PATH") or DEFAULT_SHELL_PATH,
        }
        for phase, command in (("install", install_command), ("init", init_command)):
            try:
                completed = runner(
                    command,
                    cwd=workdir,
                    env=env,
                    timeout=LOAD_TEST_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                status = "failed"
                error = "load_test_timeout"
                infrastructure_error = True
                chunks.append(
                    f"$ {phase}: {command}\n超时（{LOAD_TEST_TIMEOUT_SECONDS}s）"
                )
                break
            except (OSError, subprocess.SubprocessError) as exc:
                status = "failed"
                error = "load_test_execution_failed"
                infrastructure_error = True
                chunks.append(f"$ {phase}: {command}\n执行失败：{exc}")
                break
            output = "\n".join(
                part.strip()
                for part in (
                    getattr(completed, "stdout", ""),
                    getattr(completed, "stderr", ""),
                )
                if part and part.strip()
            )
            _append_command_log(chunks, phase, command, output)
            if int(getattr(completed, "returncode", 1)) != 0:
                status = "failed"
                error = f"{phase}_command_failed"
                break

        if status == "passed":
            try:
                generated = _scan_generated_assets(Path(workdir))
            except AgentAssetError as exc:
                status = "failed"
                error = exc.code
                chunks.append(f"$ scan\n{exc}")
            else:
                if not generated:
                    status = "failed"
                    error = "no_generated_assets"
                    chunks.append("$ scan\n命令执行成功，但未发现生成的 Skill/Command/Subagent")

    result: dict[str, Any] = {
        "status": status,
        "duration_seconds": round(time.monotonic() - started, 3),
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "install_command": install_command,
        "init_command": init_command,
        "generated": generated,
        "log_tail": _log_tail(chunks),
    }
    if prerequisites.get("runtime"):
        result["runtime"] = prerequisites["runtime"]
    if error:
        result["error"] = error
    return result, infrastructure_error


def _load_test_fingerprint(
    repo_url: str,
    requested_ref: str,
    scan_paths: list[str],
    prerequisites: dict[str, str],
) -> str:
    payload = json.dumps(
        {
            "repo_url": repo_url,
            "requested_ref": requested_ref,
            "scan_paths": scan_paths,
            "runtime_prerequisites": prerequisites,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_load_test_result(token: str, fingerprint: str, result: dict[str, Any]) -> None:
    now = time.monotonic()
    with _LOAD_TEST_CACHE_LOCK:
        for key, (expires_at, _entry) in list(_LOAD_TEST_CACHE.items()):
            if expires_at <= now:
                _LOAD_TEST_CACHE.pop(key, None)
        _LOAD_TEST_CACHE[token] = (
            now + LOAD_TEST_TOKEN_TTL_SECONDS,
            {"fingerprint": fingerprint, "result": result},
        )


def _cached_load_test_result(token: str | None, fingerprint: str) -> dict[str, Any] | None:
    if not token:
        return None
    now = time.monotonic()
    with _LOAD_TEST_CACHE_LOCK:
        entry = _LOAD_TEST_CACHE.get(token)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at <= now:
            _LOAD_TEST_CACHE.pop(token, None)
            return None
        if payload.get("fingerprint") != fingerprint:
            return None
        return payload.get("result")


def preview_load_test(
    *,
    repo_url: str,
    requested_ref: str,
    scan_paths: list[str] | None = None,
    runtime_prerequisites: dict[str, Any] | None = None,
    runner=None,
) -> tuple[dict[str, Any], str]:
    """Run the add-source load test without persisting a source row."""

    parse_repo_url(repo_url)
    ref = (requested_ref or "").strip()
    if not ref:
        raise AgentAssetError("ref_required", "Ref 不能为空", field="requested_ref")
    prerequisites = normalize_runtime_prerequisites(runtime_prerequisites)
    if not prerequisites:
        raise AgentAssetError(
            "runtime_prerequisites_required",
            "请先配置 CLI 安装命令与初始化命令",
            field="runtime_prerequisites",
        )
    normalized_paths = _normalize_paths(scan_paths)
    result, _infrastructure_error = _execute_load_test(prerequisites, runner=runner)
    token = secrets.token_urlsafe(24)
    fingerprint = _load_test_fingerprint(
        repo_url.strip(), ref, normalized_paths, prerequisites
    )
    _cache_load_test_result(token, fingerprint, result)
    return result, token


def run_load_test(
    session: Session, source_id: str, *, runner=None
) -> tuple[AgentAssetSource, dict[str, Any]]:
    """Execute and persist a source-scoped load test with status transitions."""

    source = get_source(session, source_id)
    prerequisites = normalize_runtime_prerequisites(source.runtime_prerequisites_json)
    if not prerequisites:
        raise AgentAssetError(
            "runtime_prerequisites_required",
            "该资产源未配置运行时前置命令",
            field="runtime_prerequisites",
        )
    result, infrastructure_error = _execute_load_test(prerequisites, runner=runner)
    source.load_test_json = result
    if infrastructure_error:
        source.status = "error"
    elif result["status"] == "passed":
        source.status = "ready"
    else:
        source.status = "requires_setup"
    session.commit()
    return source, result


def _generated_source_assets(
    source: AgentAssetSource,
) -> tuple[list[ScannedAsset], dict[str, bytes]]:
    """Convert the stored generated manifest into scan assets + file bytes."""

    load_test = source.load_test_json or {}
    if load_test.get("status") != "passed":
        return [], {}
    generated = load_test.get("generated") or []
    if not isinstance(generated, list):
        raise AgentAssetError(
            "generated_assets_invalid", "加载测试生成资产格式无效", status=422
        )
    assets: list[ScannedAsset] = []
    blobs: dict[str, bytes] = {}
    seen: set[tuple[str, str]] = set()
    for item in generated:
        if not isinstance(item, dict):
            raise AgentAssetError(
                "generated_assets_invalid", "加载测试生成资产格式无效", status=422
            )
        asset_type = item.get("asset_type")
        name = str(item.get("name") or "").strip()
        path = str(item.get("path") or "").strip().strip("/")
        if asset_type not in ("skill", "command", "subagent") or not name or not path:
            raise AgentAssetError(
                "generated_assets_invalid", "加载测试生成资产缺少类型、名称或路径", status=422
            )
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
            raise AgentAssetError(
                "generated_assets_invalid", f"生成资产路径无效：{path}", status=422
            )
        key = (asset_type, path)
        if key in seen:
            raise AgentAssetError(
                "generated_assets_invalid", f"生成资产重复：{path}", status=422
            )
        seen.add(key)
        files = item.get("files")
        if not isinstance(files, list) or not files:
            raise AgentAssetError(
                "generated_assets_invalid", f"生成资产缺少文件：{path}", status=422
            )
        file_payloads: dict[str, bytes] = {}
        for file_item in files:
            if not isinstance(file_item, dict):
                raise AgentAssetError(
                    "generated_assets_invalid", f"生成资产文件格式无效：{path}", status=422
                )
            file_path = str(file_item.get("path") or "").strip().strip("/")
            file_candidate = PurePosixPath(file_path)
            if (
                not file_path
                or file_candidate.is_absolute()
                or any(part in ("", ".", "..") for part in file_candidate.parts)
            ):
                raise AgentAssetError(
                    "generated_assets_invalid", f"生成资产文件路径无效：{file_path}", status=422
                )
            content = file_item.get("content")
            if not isinstance(content, str):
                raise AgentAssetError(
                    "generated_assets_invalid", f"生成资产文件缺少内容：{file_path}", status=422
                )
            data = content.encode("utf-8")
            if file_path in blobs and blobs[file_path] != data:
                raise AgentAssetError(
                    "generated_asset_conflict", f"生成资产文件映射冲突：{file_path}", status=422
                )
            blobs[file_path] = data
            file_payloads[file_path] = data
        if not file_payloads:
            raise AgentAssetError(
                "generated_assets_invalid", f"生成资产没有文件：{path}", status=422
            )
        main_bytes = (
            _main_generated_file_bytes(asset_type, path, file_payloads, item.get("content"))
        )
        metadata: dict[str, Any] = {
            "description": _generated_description(main_bytes),
            "files": len(file_payloads),
            "size": sum(len(data) for data in file_payloads.values()),
            "runtime_path": _generated_runtime_path(asset_type, name),
            "prerequisites": [],
            "generated": True,
        }
        if asset_type == "skill":
            front = _frontmatter(_decode_text(main_bytes))
            metadata["display_name"] = front.get("name") or name
        assets.append(
            ScannedAsset(
                path=path,
                asset_type=asset_type,
                name=name,
                content_digest=_digest(main_bytes),
                metadata=metadata,
            )
        )
    return sorted(assets, key=lambda asset: (asset.asset_type, asset.path)), blobs


def _main_generated_file_bytes(
    asset_type: str, path: str, files: dict[str, bytes], content: str | None
) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if asset_type == "skill":
        manifest = f"{path}/SKILL.md"
        if manifest in files:
            return files[manifest]
    if path in files:
        return files[path]
    return next(iter(files.values()))


def _generated_runtime_path(asset_type: str, name: str) -> str:
    if asset_type == "skill":
        return f"skills/{name}"
    if asset_type == "command":
        return f"commands/{name}.md"
    return f"agents/{name}.md"


def _generated_description(content: bytes) -> str:
    front = _frontmatter(_decode_text(content))
    return front.get("description") or _command_description(content)


def _merge_tree_assets(
    git_assets: list[dict[str, Any]], generated_assets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge the Git scan with the load test's generated assets, path-unique.

    The wizard selects by path, and resolve maps the first asset with that path,
    so a Git asset keeps precedence if both describe the same file. Generated
    assets that Git did not scan are appended in manifest order.
    """

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*git_assets, *generated_assets]:
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        merged.append(item)
    return merged


def asset_tree(source: AgentAssetSource) -> dict[str, Any]:
    """The wizard's selectable tree: Git scan plus the load test's assets.

    ``last_scan_json`` is the Git scan only, so a CLI-generated source (for
    example chenmr0/OpenSpec-cn, whose repository contains no assets) used to
    show an empty table and the wizard could prepare without an asset snapshot.
    A passed load test's generated manifest is merged in with
    ``metadata.generated=true``; counts cover the merged list.
    """

    scan = source.last_scan_json or {}
    git_assets = [
        item for item in (scan.get("assets") or []) if isinstance(item, dict)
    ]
    generated_assets: list[dict[str, Any]] = []
    if _load_test_passed(source):
        generated, _files = _generated_source_assets(source)
        generated_assets = [asset.as_dict() for asset in generated]
    assets = _merge_tree_assets(git_assets, generated_assets)
    counts = {"skill": 0, "command": 0, "subagent": 0}
    for item in assets:
        asset_type = item.get("asset_type")
        if asset_type in counts:
            counts[asset_type] += 1
    return {
        "source_id": source.id,
        "scanned": bool(scan),
        "requested_ref": scan.get("requested_ref") or source.default_ref,
        "resolved_commit": scan.get("resolved_commit") or source.last_scanned_commit,
        "assets": assets,
        "unclassified": scan.get("unclassified", []),
        "counts": counts,
        "warnings": scan.get("warnings", []),
        "scanned_at": scan.get("scanned_at"),
    }


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
    root_skill = any(
        PurePosixPath(path).name == "SKILL.md" and str(PurePosixPath(path).parent) == "."
        for path in scoped
    )
    skill_dirs = {
        str(PurePosixPath(path).parent)
        for path in scoped
        if PurePosixPath(path).name == "SKILL.md"
        and str(PurePosixPath(path).parent) not in ("", ".")
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
                    "prerequisites": _runtime_prerequisites(
                        path=directory, front=front, content=_decode_text(content)
                    ),
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
            prerequisites = _runtime_prerequisites(
                path=path,
                front=_frontmatter(_decode_text(content)),
                content=_decode_text(content),
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
    if root_skill:
        warnings.append("根目录 SKILL.md 不能作为独立 Skill；请放入子目录后再扫描")
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
    load_test_token: str | None = None,
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
    prerequisites = normalize_runtime_prerequisites(runtime_prerequisites)
    # The preview token only bridges a load test that ran against this exact
    # source configuration; any changed field makes it unusable instead of
    # persisting a stale result for different commands.
    cached_load_test = None
    if load_test_token and prerequisites:
        cached_load_test = _cached_load_test_result(
            load_test_token,
            _load_test_fingerprint(
                repo_url.strip(), default_ref, scan_paths, prerequisites
            ),
        )
    if cached_load_test is not None:
        load_test_json = cached_load_test
        status = "ready" if cached_load_test.get("status") == "passed" else "requires_setup"
    else:
        load_test_json = {}
        status = "requires_setup" if prerequisites else "unverified"
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
        runtime_prerequisites_json=prerequisites,
        credential_id=credential_id,
        status=status,
        last_scan_json={},
        load_test_json=load_test_json,
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
    source.status = _source_status(source, result.assets)
    source.last_scan_json = _scan_payload(result, ref)
    session.commit()
    return result


# ---------------------------------------------------------------------------
# Resolve + snapshot packaging
# ---------------------------------------------------------------------------


def _selection_digest(
    commit: str,
    runtime_key: str,
    assets: list[ScannedAsset],
    generated_files: dict[str, bytes] | None = None,
) -> str:
    document: dict[str, Any] = {
        "commit": commit,
        "runtime": runtime_key,
        "assets": sorted([asset.asset_type, asset.path] for asset in assets),
    }
    # Keep non-CLI snapshot digests identical to previous releases. A new load
    # test can keep the same paths but change generated contents; hashing the
    # files makes that produce a new frozen snapshot.
    if generated_files:
        document["generated"] = {
            path: _digest(content)
            for path, content in sorted(generated_files.items())
        }
    payload = json.dumps(
        document,
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


def _source_path_size(
    path: str, blobs: dict[str, Any], generated_files: dict[str, bytes]
) -> int:
    if path in generated_files:
        return len(generated_files[path])
    entry = blobs.get(path)
    return int(entry.size or 0) if entry is not None else 0


def _asset_destinations(
    asset: ScannedAsset, blobs: dict[str, Any], generated_files: dict[str, bytes]
) -> list[str]:
    if asset.asset_type == "skill":
        source_paths = generated_files if asset.metadata.get("generated") else blobs
        owned = sorted(
            path
            for path in source_paths
            if path == asset.path or path.startswith(asset.path + "/")
        )
        return [f"skills/{asset.name}/{path[len(asset.path) + 1:]}" for path in owned]
    return [str(asset.metadata.get("runtime_path"))]


def _check_generated_conflicts(
    scan_assets: list[ScannedAsset] | tuple[ScannedAsset, ...],
    generated_assets: list[ScannedAsset],
    blobs: dict[str, Any],
    generated_files: dict[str, bytes],
) -> None:
    """Reject ambiguous bundle mappings instead of silently overriding files.

    Generated assets are merged into every snapshot. If the Git scan already
    defines the same runtime destination (even an unselected one), the load
    test output is ambiguous: generated assets win only when Git has none, and
    otherwise the operator gets a clear conflict instead of a silent override.
    """

    git_destinations: set[str] = set()
    for asset in scan_assets:
        if asset.metadata.get("generated"):
            continue
        git_destinations.update(
            _asset_destinations(asset, blobs, generated_files)
        )

    generated_destinations: set[str] = set()
    for asset in generated_assets:
        for destination in _asset_destinations(asset, blobs, generated_files):
            if destination in git_destinations:
                raise AgentAssetError(
                    "generated_asset_conflict",
                    f"生成资产与 Git 扫描资产路径冲突：{destination}",
                    details={"path": destination},
                )
            if destination in generated_destinations:
                raise AgentAssetError(
                    "generated_asset_conflict",
                    f"生成资产文件映射冲突：{destination}",
                    details={"path": destination},
                )
            generated_destinations.add(destination)


def _merge_generated_assets(
    selected: list[ScannedAsset], generated_assets: list[ScannedAsset]
) -> list[ScannedAsset]:
    """Generated assets are part of a CLI source's load test, not optional."""

    merged = list(selected)
    seen = {(asset.asset_type, asset.path) for asset in merged}
    for asset in generated_assets:
        key = (asset.asset_type, asset.path)
        if key not in seen:
            seen.add(key)
            merged.append(asset)
    return merged


def _select_assets(
    *,
    scan: ScanResult,
    blobs: dict[str, Any],
    paths: list[str],
    types: dict[str, str] | None,
    generated_assets: list[ScannedAsset] | None = None,
    generated_files: dict[str, bytes] | None = None,
) -> list[ScannedAsset]:
    types = types or {}
    generated_assets = generated_assets or []
    generated_files = generated_files or {}
    available = [*scan.assets, *generated_assets]
    selected: list[ScannedAsset] = []
    by_path: dict[str, ScannedAsset] = {}
    for asset in available:
        by_path.setdefault(asset.path, asset)
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
        prefixed = [asset for asset in available if asset.path.startswith(prefix)]
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
            if manifest not in blobs and manifest not in generated_files:
                raise AgentAssetError(
                    "skill_manifest_missing", f"Skill 目录缺少 SKILL.md：{path}", details={"path": path}
                )
            owned = sorted(
                candidate
                for candidate in [*blobs, *generated_files]
                if candidate == path or candidate.startswith(prefix)
            )
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
                        "size": sum(
                            _source_path_size(candidate, blobs, generated_files)
                            for candidate in owned
                        ),
                        "runtime_path": f"skills/{_skill_name_from_path(path)}",
                        "prerequisites": [],
                        "explicit": True,
                    },
                )
            )
            continue
        if path not in blobs and path not in generated_files:
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
                    "size": _source_path_size(path, blobs, generated_files),
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
    generated_files: dict[str, bytes] | None = None,
) -> tuple[dict[str, bytes], list[dict[str, Any]], str]:
    owner, repo = parse_repo_url(source.repo_url)
    generated_files = generated_files or {}
    planned: set[str] = set()
    for asset in selected:
        if asset.asset_type == "skill":
            source_paths = generated_files if asset.metadata.get("generated") else blobs
            planned.update(
                path
                for path in source_paths
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
    planned_bytes = sum(
        _source_path_size(path, blobs, generated_files) for path in planned
    )
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
        if path in generated_files:
            return generated_files[path]
        entry = blobs[path]
        cached = scan.blob_cache.get(entry.sha)
        if cached is None:
            cached = client.blob(owner, repo, entry.sha)
            scan.blob_cache[entry.sha] = cached
        return cached

    for asset in sorted(selected, key=lambda item: (item.asset_type, item.path)):
        asset_files: list[dict[str, Any]] = []
        is_generated = bool(asset.metadata.get("generated"))
        mode_for = (lambda path: "100644") if is_generated else (lambda path: modes.get(path, "100644"))
        if asset.asset_type == "skill":
            source_paths = generated_files if is_generated else blobs
            owned = sorted(
                path
                for path in source_paths
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
                        "mode": mode_for(path),
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
                    "mode": mode_for(asset.path),
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
        generated_assets, generated_files = _generated_source_assets(source)
        _check_generated_conflicts(scan.assets, generated_assets, blobs, generated_files)
        selected = _select_assets(
            scan=scan,
            blobs=blobs,
            paths=paths,
            types=types,
            generated_assets=generated_assets,
            generated_files=generated_files,
        )
        selected = _merge_generated_assets(selected, generated_assets)
        digest = _selection_digest(commit, runtime_key, selected, generated_files)
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
            source.status = _source_status(source, scan.assets)
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
            generated_files=generated_files,
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

        load_test = load_test_view(source.load_test_json or {})
        runtime_prerequisites = dict(source.runtime_prerequisites_json or {})
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
            "runtime_prerequisites": runtime_prerequisites,
            "load_test": load_test,
            # M8-10: the runner consumes this from the frozen run payload, so
            # freeze it here with the rest of the preparation inputs.
            "runtime_setup": runtime_setup_view(
                {"runtime_prerequisites": runtime_prerequisites, "load_test": load_test}
            ),
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
                {
                    "asset_type": asset.asset_type,
                    "path": asset.path,
                    "name": asset.name,
                    **({"generated": True} if asset.metadata.get("generated") else {}),
                }
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
        source.status = _source_status(source, scan.assets)
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
        "runtime_prerequisites": (snapshot.manifest_json or {}).get(
            "runtime_prerequisites"
        )
        or {},
        "load_test": (snapshot.manifest_json or {}).get("load_test") or {},
        # M8-10: the runner executes this before starting the task, so it must
        # travel with the frozen snapshot rather than the live source row.
        "runtime_setup": runtime_setup_view(snapshot.manifest_json),
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
        "load_test": load_test_view(source.load_test_json or {}),
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
    # M8-10: an older run row may predate runtime_setup; keep the key stable so
    # the runner can always check a single field before executing prerequisites.
    payload.setdefault("runtime_setup", {})
    key = payload.get("bundle_key")
    if not key:
        return payload
    try:
        store = _object_store(settings)
        payload["bundle_url"] = store.presign_get_url(str(key), expires=3600)
    except (AgentAssetError, ObjectStoreError):
        payload["bundle_error"] = "object_store_unavailable"
    return payload
