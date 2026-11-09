"""Runtime adapter protocol and value objects (M3-03, design 07 §4).

The adapter is deliberately small: validate an immutable asset snapshot,
materialize it into an isolated runtime home, produce a structured command
(no shell), turn the native stream into minimal live events, and discover the
authoritative trace files. Trace parsing and parent/child relations belong to
M6.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AssetIssue:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class AssetSnapshot:
    """Immutable files keyed by a path under the runtime asset root."""

    files: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeTask:
    prompt: str
    model: str
    workspace: str
    runtime_home: str
    gateway_anthropic_base_url: str
    gateway_token: str
    max_turns: int = 40
    case_timeout_seconds: int = 3600
    execution_image_digest: str | None = None


@dataclass(frozen=True)
class CommandSpec:
    """Reviewed process launch; ``argv`` never goes through a shell."""

    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    execution_image_digest: str | None = None

    def redacted(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "env_keys": sorted(self.env),
            "execution_image_digest": self.execution_image_digest,
        }


@dataclass(frozen=True)
class LiveEvent:
    type: str
    session_id: str | None = None
    text: str | None = None
    tool_name: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceFile:
    path: str
    size: int
    sha256: str


class RuntimeAdapter(Protocol):
    runtime_key: str
    runtime_version: str
    adapter_version: str
    supported_dataset_adapters: Sequence[str]
    required_runner_labels: Sequence[str]

    def validate_assets(self, assets: AssetSnapshot) -> list[AssetIssue]: ...

    def materialize_assets(self, assets: AssetSnapshot, workspace: str) -> list[str]: ...

    def command(self, task: RuntimeTask) -> CommandSpec: ...

    def consume_stream(self, lines: Sequence[str]) -> list[LiveEvent]: ...

    def discover_trace_files(self, runtime_home: str) -> list[TraceFile]: ...
