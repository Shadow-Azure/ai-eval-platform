"""M3-03 Claude Code runtime adapter: assets, command, stream and traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.runtimes.base import AssetSnapshot, RuntimeTask
from app.runtimes.claude_code import ClaudeCodeAdapter, get_runtime_adapter


def _snapshot() -> AssetSnapshot:
    return AssetSnapshot(
        files={
            "skills/brainstorming/SKILL.md": b"# brainstorming\n",
            "skills/brainstorming/notes.md": b"notes\n",
            "commands/plan.md": b"# plan\n",
            "agents/reviewer.md": b"# reviewer\n",
        }
    )


def test_valid_assets_pass_validation_and_materialize(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()
    assert adapter.validate_assets(_snapshot()) == []
    written = adapter.materialize_assets(_snapshot(), str(tmp_path))
    assert written == [
        ".claude/agents/reviewer.md",
        ".claude/commands/plan.md",
        ".claude/skills/brainstorming/SKILL.md",
        ".claude/skills/brainstorming/notes.md",
    ]
    assert (tmp_path / ".claude/skills/brainstorming/SKILL.md").read_text() == "# brainstorming\n"


@pytest.mark.parametrize(
    "path,code",
    [
        ("../evil.md", "asset_path_invalid"),
        ("/etc/passwd", "asset_path_invalid"),
        ("docs/readme.md", "asset_path_not_allowed"),
        ("commands/plan.txt", "asset_extension_invalid"),
    ],
)
def test_invalid_assets_are_rejected(path: str, code: str) -> None:
    issues = ClaudeCodeAdapter().validate_assets(AssetSnapshot(files={path: b"x"}))
    assert any(issue.code == code for issue in issues)


def test_empty_asset_is_rejected() -> None:
    issues = ClaudeCodeAdapter().validate_assets(
        AssetSnapshot(files={"commands/empty.md": b""})
    )
    assert [issue.code for issue in issues] == ["asset_empty"]


def test_skill_without_skill_md_is_rejected() -> None:
    issues = ClaudeCodeAdapter().validate_assets(
        AssetSnapshot(files={"skills/brainstorming/notes.md": b"notes"})
    )
    assert [issue.code for issue in issues] == ["skill_manifest_missing"]


def test_materialize_refuses_invalid_snapshot(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ClaudeCodeAdapter().materialize_assets(
            AssetSnapshot(files={"../evil": b"x"}), str(tmp_path)
        )


def test_command_is_structured_and_never_uses_a_shell(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()
    prompt = "fix the bug; rm -rf / # not a shell"
    task = RuntimeTask(
        prompt=prompt,
        model="MiniMax-M2.7-highspeed",
        workspace=str(tmp_path),
        runtime_home=str(tmp_path / "home"),
        gateway_anthropic_base_url="http://127.0.0.1:8000/model-gateway/anthropic",
        gateway_token="v1.token.signature",
    )
    spec = adapter.command(task)
    assert spec.argv[0] == "claude"
    assert spec.argv[2] == prompt
    assert "--output-format" in spec.argv and "stream-json" in spec.argv
    assert "--bare" not in spec.argv
    assert "--dangerously-skip-permissions" in spec.argv
    assert spec.cwd == str(tmp_path)
    assert spec.env["ANTHROPIC_BASE_URL"].endswith("/model-gateway/anthropic")
    assert spec.env["ANTHROPIC_AUTH_TOKEN"] == "v1.token.signature"
    assert spec.env["HOME"] == str(tmp_path / "home")
    assert "provider-key" not in json.dumps(spec.env)
    assert "ANTHROPIC_AUTH_TOKEN" in spec.redacted()["env_keys"]


def test_command_requires_gateway_token(tmp_path: Path) -> None:
    task = RuntimeTask(
        prompt="hi",
        model="MiniMax-M2.7-highspeed",
        workspace=str(tmp_path),
        runtime_home=str(tmp_path / "home"),
        gateway_anthropic_base_url="http://127.0.0.1:8000/model-gateway/anthropic",
        gateway_token="",
    )
    with pytest.raises(ValueError):
        ClaudeCodeAdapter().command(task)


def test_consume_stream_maps_minimal_live_events() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "model": "m"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I will inspect the test."},
                        {"type": "tool_use", "name": "Read"},
                    ]
                },
            }
        ),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"text": "ok"}}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3}),
        "not-json",
        "",
    ]
    events = ClaudeCodeAdapter().consume_stream(lines)
    assert [event.type for event in events] == [
        "session_started",
        "assistant_message",
        "text_delta",
        "completed",
    ]
    assert events[0].session_id == "s1"
    assert events[1].tool_name == "Read"
    assert events[-1].payload["num_turns"] == 3


def test_discover_trace_files_returns_digest_and_size(tmp_path: Path) -> None:
    session = tmp_path / ".claude/projects/-workspace/session-1.jsonl"
    session.parent.mkdir(parents=True)
    payload = b'{"type":"user"}\n'
    session.write_bytes(payload)
    traces = ClaudeCodeAdapter().discover_trace_files(str(tmp_path))
    assert len(traces) == 1
    assert traces[0].path == ".claude/projects/-workspace/session-1.jsonl"
    assert traces[0].size == len(payload)
    assert traces[0].sha256 == hashlib.sha256(payload).hexdigest()


def test_adapter_registry() -> None:
    assert get_runtime_adapter("claude-code") is not None
    assert get_runtime_adapter("unknown") is None
