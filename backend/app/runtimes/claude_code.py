"""Claude Code runtime adapter (M3-03).

Only assets named by the immutable snapshot are materialized. The runtime
uses an isolated HOME and CLAUDE_CONFIG_DIR so a stale host configuration can
never be loaded; ``--bare`` is intentionally not used because it disables the
Skill/Command/Subagent discovery this runtime exists to exercise.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from .base import (
    AssetIssue,
    AssetSnapshot,
    CommandSpec,
    LiveEvent,
    RuntimeTask,
    TraceFile,
)

ALLOWED_ROOTS = ("skills", "commands", "agents")
MAX_ASSETS = 256
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_EVENT_TEXT = 4000


def _safe_relative(path: str) -> PurePosixPath | None:
    if not path or "\x00" in path or "\\" in path:
        return None
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        return None
    return candidate


def _trim(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:MAX_EVENT_TEXT]


class ClaudeCodeAdapter:
    runtime_key = "claude-code"
    runtime_version = "2.x"
    adapter_version = "claude-code-1.0"
    supported_dataset_adapters = ("swebench",)
    required_runner_labels = ("linux",)

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def validate_assets(self, assets: AssetSnapshot) -> list[AssetIssue]:
        issues: list[AssetIssue] = []
        if len(assets.files) > MAX_ASSETS:
            issues.append(
                AssetIssue(
                    "asset_count_exceeded",
                    f"资产文件数 {len(assets.files)} 超过上限 {MAX_ASSETS}",
                )
            )
        total = sum(len(content) for content in assets.files.values())
        if total > MAX_TOTAL_BYTES:
            issues.append(
                AssetIssue(
                    "asset_size_exceeded",
                    f"资产总大小 {total} 超过上限 {MAX_TOTAL_BYTES}",
                )
            )

        skill_roots: set[str] = set()
        for raw_path, content in assets.files.items():
            relative = _safe_relative(raw_path)
            if relative is None:
                issues.append(
                    AssetIssue("asset_path_invalid", "资产路径必须是安全的相对路径", raw_path)
                )
                continue
            parts = relative.parts
            if not parts or parts[0] not in ALLOWED_ROOTS:
                issues.append(
                    AssetIssue(
                        "asset_path_not_allowed",
                        "只允许 skills/、commands/、agents/ 下的资产",
                        raw_path,
                    )
                )
                continue
            if parts[0] == "skills":
                if len(parts) < 2:
                    issues.append(AssetIssue("skill_root_invalid", "Skill 必须以目录为单位", raw_path))
                    continue
                skill_roots.add(parts[1])
                if not content:
                    issues.append(AssetIssue("asset_empty", "资产文件不能为空", raw_path))
            elif parts[0] in ("commands", "agents"):
                if len(parts) < 2 or not parts[-1].endswith(".md"):
                    issues.append(
                        AssetIssue("asset_extension_invalid", f"{parts[0]} 资产必须是 .md 文件", raw_path)
                    )
                if not content:
                    issues.append(AssetIssue("asset_empty", "资产文件不能为空", raw_path))

        for root in sorted(skill_roots):
            skill_md = f"skills/{root}/SKILL.md"
            if skill_md not in assets.files:
                issues.append(
                    AssetIssue("skill_manifest_missing", "每个 Skill 目录必须包含 SKILL.md", skill_md)
                )
        return issues

    def materialize_assets(self, assets: AssetSnapshot, workspace: str) -> list[str]:
        issues = self.validate_assets(assets)
        if issues:
            raise ValueError("资产校验失败：" + "; ".join(issue.message for issue in issues))
        root = Path(workspace) / ".claude"
        written: list[str] = []
        for raw_path, content in sorted(assets.files.items()):
            relative = _safe_relative(raw_path)
            assert relative is not None
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            written.append(str(PurePosixPath(".claude") / relative))
        return written

    # ------------------------------------------------------------------
    # Command + stream
    # ------------------------------------------------------------------

    def command(self, task: RuntimeTask) -> CommandSpec:
        if not task.prompt.strip():
            raise ValueError("任务 prompt 不能为空")
        if not task.model.strip():
            raise ValueError("任务 model 不能为空")
        if not task.gateway_token:
            raise ValueError("缺少模型网关短期 token")
        if not task.gateway_anthropic_base_url.startswith(("http://", "https://")):
            raise ValueError("模型网关 base URL 无效")
        argv = (
            "claude",
            "-p",
            task.prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            task.model,
            "--max-turns",
            str(max(1, int(task.max_turns))),
            "--permission-mode",
            "acceptEdits",
            # The sandbox is a disposable non-root container; the reviewed
            # template needs Bash/Edit tools without an interactive prompt.
            "--dangerously-skip-permissions",
        )
        env = {
            "HOME": task.runtime_home,
            "CLAUDE_CONFIG_DIR": str(Path(task.runtime_home) / ".claude"),
            "ANTHROPIC_BASE_URL": task.gateway_anthropic_base_url.rstrip("/"),
            "ANTHROPIC_AUTH_TOKEN": task.gateway_token,
            "ANTHROPIC_MODEL": task.model,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
        }
        return CommandSpec(
            argv=argv,
            cwd=task.workspace,
            env=env,
            execution_image_digest=task.execution_image_digest,
        )

    def consume_stream(self, lines: Sequence[str]) -> list[LiveEvent]:
        events: list[LiveEvent] = []
        session_id: str | None = None
        for raw in lines:
            text = raw.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "system":
                session_id = item.get("session_id") or item.get("sessionId") or session_id
                events.append(
                    LiveEvent(
                        type="session_started",
                        session_id=session_id,
                        payload={"model": item.get("model")},
                    )
                )
            elif kind == "assistant":
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                text_parts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                tools = [
                    part.get("name")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "tool_use"
                ]
                events.append(
                    LiveEvent(
                        type="assistant_message",
                        session_id=session_id,
                        text=_trim("".join(text_parts)),
                        tool_name=tools[0] if tools else None,
                    )
                )
            elif kind == "user":
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        events.append(
                            LiveEvent(
                                type="tool_result",
                                session_id=session_id,
                                payload={"is_error": bool(part.get("is_error"))},
                            )
                        )
            elif kind == "result":
                session_id = item.get("session_id") or session_id
                events.append(
                    LiveEvent(
                        type="completed",
                        session_id=session_id,
                        payload={
                            "subtype": item.get("subtype"),
                            "is_error": bool(item.get("is_error")),
                            "num_turns": item.get("num_turns"),
                        },
                    )
                )
            elif kind == "stream_event":
                event = item.get("event") if isinstance(item.get("event"), dict) else {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                    text = delta.get("text")
                    if isinstance(text, str):
                        events.append(
                            LiveEvent(type="text_delta", session_id=session_id, text=_trim(text))
                        )
        return events

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    def discover_trace_files(self, runtime_home: str) -> list[TraceFile]:
        root = Path(runtime_home) / ".claude"
        if not root.exists():
            return []
        traces: list[TraceFile] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if not (path.name.endswith(".jsonl") or path.name.endswith(".meta.json")):
                continue
            data = path.read_bytes()
            traces.append(
                TraceFile(
                    path=str(path.relative_to(runtime_home)),
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
        return traces


def get_runtime_adapter(runtime_key: str):
    if runtime_key in (ClaudeCodeAdapter.runtime_key, "claude_code"):
        return ClaudeCodeAdapter()
    return None
