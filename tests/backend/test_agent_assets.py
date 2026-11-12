"""M5-01 Agent asset sources, immutable snapshots and eval freezing.

The GitHub client is faked at the service boundary: these tests assert the
platform's scan/selection/packaging contract, not GitHub's HTTP behaviour.
Snapshot bundles use the local S3 stub so the real presigned PUT/GET path runs.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.config import get_settings
from app.models import AgentAssetSnapshot, AgentAssetSource, Credential, EvalRun
from app.seed import ensure_default_scope
from app.services import agent_assets
from app.services.git_client import TreeEntry
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy.orm import Session

COMMIT_ONE = "a" * 40
COMMIT_TWO = "b" * 40


@dataclass
class FakeRepo:
    commit: str = COMMIT_ONE
    files: dict[str, bytes] | None = None

    def __post_init__(self) -> None:
        if self.files is None:
            self.files = {
                "skills/brainstorming/SKILL.md": (
                    b"---\nname: brainstorming\ndescription: Explore options\n---\n# Brainstorm\n"
                ),
                "skills/brainstorming/notes.md": b"notes\n",
                "skills/tdd/SKILL.md": b"# TDD\n",
                "skills/tdd/reference.md": b"reference\n",
                ".claude/commands/ship-check.md": b"# Ship check\n",
                ".claude/commands/opsx/propose.md": b"# openspec propose\n",
                ".claude/agents/test-runner.md": b"# Test runner\n",
                "docs/readme.md": b"# docs\n",
                "src/commands/deploy.md": b"# Not a runtime command\n",
                "SKILL.md": b"# Root skill manifest\n",
            }


class FakeGitHubClient:
    def __init__(self, repo: FakeRepo, token: str | None = None) -> None:
        self.repo = repo
        self.token = token
        self.closed = False
        self.blobs = {
            hashlib.sha1(path.encode()).hexdigest(): content
            for path, content in repo.files.items()
        }

    def close(self) -> None:
        self.closed = True

    def resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        return self.repo.commit

    def tree(self, owner: str, repo: str, commit: str) -> list[TreeEntry]:
        return [
            TreeEntry(
                path=path,
                entry_type="blob",
                sha=hashlib.sha1(path.encode()).hexdigest(),
                size=len(content),
            )
            for path, content in self.repo.files.items()
        ]

    def blob(self, owner: str, repo: str, sha: str) -> bytes:
        return self.blobs[sha]


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo):
    factory = lambda token=None, **_kwargs: FakeGitHubClient(repo, token)  # noqa: E731
    monkeypatch.setattr(agent_assets, "GitHubClient", factory)
    return repo


@pytest.fixture
def asset_store(configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_BUCKET="eval-artifacts",
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
            S3_ADDRESSING_STYLE="path",
        )
        yield store


@pytest.fixture
def seeded(session: Session) -> Session:
    ensure_default_scope(session)
    return session


def create_source(client: TestClient, **overrides: object) -> dict:
    payload = {
        "name": "superpowers",
        "repo_url": "https://github.com/obra/superpowers",
        "default_ref": "main",
        "scan_paths": ["skills", ".claude", "docs", "src"],
        "selection_mode": "explicit",
        "description": "workflow skills",
        **overrides,
    }
    response = client.post("/api/v1/agent-asset-sources", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def scan_source(client: TestClient, source_id: str) -> dict:
    response = client.post(f"/api/v1/agent-asset-sources/{source_id}/scan", json={})
    assert response.status_code == 200, response.text
    return response.json()


def resolve(client: TestClient, source_id: str, paths: list[str], **overrides: object) -> dict:
    response = client.post(
        "/api/v1/agent-assets/resolve",
        json={"source_id": source_id, "paths": paths, **overrides},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_create_scan_and_tree_roundtrip(
    client: TestClient, seeded: Session, fake_git: FakeRepo
) -> None:
    source = create_source(client)
    assert source["status"] == "unverified"
    assert source["counts"] == {"skill": 0, "command": 0, "subagent": 0}

    scanned = scan_source(client, source["id"])
    assert scanned["resolved_commit"] == COMMIT_ONE
    assert scanned["counts"] == {"skill": 2, "command": 2, "subagent": 1}
    # A random src/commands folder is not a native runtime layout: it must stay
    # unclassified and require an explicit type.
    assert all(asset["path"] != "src/commands/deploy.md" for asset in scanned["assets"])
    names = {(asset["asset_type"], asset["path"]) for asset in scanned["assets"]}
    assert ("skill", "skills/brainstorming") in names
    assert ("command", ".claude/commands/opsx/propose.md") in names
    assert ("subagent", ".claude/agents/test-runner.md") in names
    command = next(asset for asset in scanned["assets"] if asset["name"] == "opsx/propose")
    assert command["metadata"]["prerequisites"] == ["openspec CLI"]

    tree = client.get(f"/api/v1/agent-asset-sources/{source['id']}/tree").json()
    assert tree["scanned"] is True
    assert tree["counts"]["skill"] == 2
    assert any(item["path"] == "docs/readme.md" for item in tree["unclassified"])
    assert any(item["path"] == "src/commands/deploy.md" for item in tree["unclassified"])
    # The opsx command declares a CLI dependency, so the source needs runtime
    # preparation even though the scan found assets.
    refreshed = client.get(f"/api/v1/agent-asset-sources/{source['id']}").json()
    assert refreshed["status"] == "requires_setup"


def _install_repo(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> FakeRepo:
    repo = FakeRepo(files=files)
    factory = lambda token=None, **_kwargs: FakeGitHubClient(repo, token)  # noqa: E731
    monkeypatch.setattr(agent_assets, "GitHubClient", factory)
    return repo


OPENSPEC_FILES = {
    "skills/openspec-explore/SKILL.md": (
        b"---\n"
        b"name: openspec-explore\n"
        b"description: Explore OpenSpec changes\n"
        b"compatibility: Requires openspec CLI.\n"
        b"---\n# Explore\n"
    ),
    "skills/brainstorming/SKILL.md": (
        b"---\nname: brainstorming\ndescription: Explore options\n---\n# Brainstorm\n"
    ),
    ".claude/commands/opsx/propose.md": b"# openspec propose\n",
}

PLAIN_FILES = {
    "skills/plain/SKILL.md": (
        b"---\nname: plain\ndescription: No runtime dependency\n---\n# Plain\n"
    ),
}


def test_scan_marks_runtime_prerequisites(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_repo(monkeypatch, dict(OPENSPEC_FILES))
    source = create_source(
        client, name="openspec", scan_paths=["skills", ".claude"], selection_mode="directory"
    )
    scanned = scan_source(client, source["id"])
    assert scanned["counts"] == {"skill": 2, "command": 1, "subagent": 0}
    by_path = {asset["path"]: asset for asset in scanned["assets"]}
    assert by_path["skills/openspec-explore"]["metadata"]["prerequisites"] == ["openspec CLI"]
    assert by_path["skills/brainstorming"]["metadata"]["prerequisites"] == []
    assert by_path[".claude/commands/opsx/propose.md"]["metadata"]["prerequisites"] == [
        "openspec CLI"
    ]

    refreshed = client.get(f"/api/v1/agent-asset-sources/{source['id']}").json()
    assert refreshed["status"] == "requires_setup"


def test_scan_without_prerequisites_is_ready(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_repo(monkeypatch, dict(PLAIN_FILES))
    source = create_source(
        client, name="plain", scan_paths=["skills"], selection_mode="directory"
    )
    scan_source(client, source["id"])
    refreshed = client.get(f"/api/v1/agent-asset-sources/{source['id']}").json()
    assert refreshed["status"] == "ready"


def test_scan_preview_does_not_persist_source(
    client: TestClient, seeded: Session, fake_git: FakeRepo
) -> None:
    response = client.post(
        "/api/v1/agent-asset-sources/scan-preview",
        json={
            "repo_url": "https://github.com/obra/superpowers",
            "requested_ref": "main",
            "scan_paths": ["skills", ".claude", "docs", "src"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["counts"]["skill"] == 2
    assert client.get("/api/v1/agent-asset-sources").json()["items"] == []


def test_git_token_is_write_only_and_encrypted(
    client: TestClient, seeded: Session, fake_git: FakeRepo
) -> None:
    source = create_source(client, access_token="ghp_supersecret1234")
    assert source["credential_mask"] == "****1234"
    assert source["uses_platform_token"] is False
    # The response never carries the token; the credential row is a single
    # encrypted value with a mask, not the plaintext.
    stored = seeded.query(Credential).all()
    assert stored and all("ghp_supersecret" not in item.ciphertext for item in stored)
    assert source["credential_mask"] not in ("ghp_supersecret1234",)


def test_resolve_is_idempotent_and_bundles_assets(
    client: TestClient, seeded: Session, fake_git: FakeRepo, asset_store: StubObjectStore
) -> None:
    source = create_source(client)
    scan_source(client, source["id"])
    first = resolve(
        client,
        source["id"],
        ["skills/brainstorming", ".claude/commands/ship-check.md", ".claude/agents/test-runner.md"],
    )
    snapshot = first["snapshot"]
    assert first["created"] is True
    assert snapshot["resolved_commit"] == COMMIT_ONE
    assert snapshot["asset_count"] == 3
    assert snapshot["file_count"] == 4
    assert snapshot["bundle_digest"].startswith("sha256:")
    assert snapshot["tree_digest"].startswith("sha256:")
    assert asset_store.has(snapshot["bundle_key"])
    bundle = asset_store.bodies[f"/eval-artifacts/{snapshot['bundle_key']}"]
    assert bundle[:2] == b"\x1f\x8b"
    mapped = {asset["path"]: asset for asset in snapshot["assets"]}
    assert mapped["skills/brainstorming"]["metadata"]["file_count"] == 2
    assert mapped[".claude/commands/ship-check.md"]["metadata"]["runtime_path"] == "commands/ship-check.md"

    second = resolve(
        client,
        source["id"],
        ["skills/brainstorming", ".claude/commands/ship-check.md", ".claude/agents/test-runner.md"],
    )
    assert second["created"] is False
    assert second["snapshot"]["id"] == snapshot["id"]
    assert seeded.query(AgentAssetSnapshot).count() == 1


def test_directory_selection_expands_to_all_assets(
    client: TestClient, seeded: Session, fake_git: FakeRepo, asset_store: StubObjectStore
) -> None:
    source = create_source(client)
    scan_source(client, source["id"])
    resolved = resolve(client, source["id"], ["skills"])
    assert resolved["snapshot"]["asset_count"] == 2


def test_unclassified_path_requires_explicit_type(
    client: TestClient, seeded: Session, fake_git: FakeRepo, asset_store: StubObjectStore
) -> None:
    source = create_source(client)
    scan_source(client, source["id"])
    response = client.post(
        "/api/v1/agent-assets/resolve",
        json={"source_id": source["id"], "paths": ["docs/readme.md"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "asset_type_unresolved"

    resolved = resolve(
        client,
        source["id"],
        ["docs/readme.md"],
        types={"docs/readme.md": "command"},
    )
    assert resolved["snapshot"]["assets"][0]["metadata"]["runtime_path"] == "commands/readme.md"


def test_snapshot_survives_branch_move_and_is_frozen_on_run(
    client: TestClient,
    seeded: Session,
    fake_git: FakeRepo,
    asset_store: StubObjectStore,
) -> None:
    source = create_source(client)
    scan_source(client, source["id"])
    snapshot_id = resolve(client, source["id"], ["skills/brainstorming"])["snapshot"]["id"]

    # The branch moves and files disappear; the frozen snapshot still points at
    # the original commit and its digest.
    fake_git.commit = COMMIT_TWO
    fake_git.files.pop("skills/brainstorming/notes.md", None)
    response = client.post(
        "/api/v1/evaluations",
        json={"case_keys": ["case-a"], "asset_snapshot_id": snapshot_id},
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    assert body["asset_snapshot_id"] == snapshot_id
    assert body["asset_resolved_commit"] == COMMIT_ONE
    run = seeded.get(EvalRun, body["id"])
    assert run is not None
    assert run.asset_snapshot_id == snapshot_id
    assert run.asset_snapshot_json["resolved_commit"] == COMMIT_ONE
    assert run.asset_snapshot_json["asset_count"] == 1


def test_snapshot_view_and_task_presign(
    client: TestClient, seeded: Session, fake_git: FakeRepo, asset_store: StubObjectStore
) -> None:
    source = create_source(client)
    scan_source(client, source["id"])
    snapshot = resolve(client, source["id"], ["skills/brainstorming"])["snapshot"]
    detail = client.get(f"/api/v1/agent-asset-snapshots/{snapshot['id']}")
    assert detail.status_code == 200
    assert detail.json()["assets"][0]["name"] == "brainstorming"

    payload = agent_assets.task_asset_payload(
        {"bundle_key": snapshot["bundle_key"]}, get_settings()
    )
    assert payload["bundle_url"].startswith("http://")
    assert "X-Amz-Signature=" in payload["bundle_url"]


def test_object_store_missing_is_a_blocker(
    client: TestClient, seeded: Session, fake_git: FakeRepo, configure_settings
) -> None:
    configure_settings(S3_ENDPOINT="", S3_ACCESS_KEY="", S3_SECRET_KEY="")
    source = create_source(client)
    scan_source(client, source["id"])
    response = client.post(
        "/api/v1/agent-assets/resolve",
        json={"source_id": source["id"], "paths": ["skills/brainstorming"]},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "object_store_unavailable"


def test_root_skill_manifest_is_reported_not_crashed(monkeypatch: pytest.MonkeyPatch, repo: FakeRepo) -> None:
    monkeypatch.setattr(
        agent_assets, "GitHubClient", lambda token=None, **_kwargs: FakeGitHubClient(repo, token)
    )
    result = agent_assets.scan_repository(
        client=FakeGitHubClient(repo),
        repo_url="https://github.com/obra/superpowers",
        requested_ref="main",
        scan_paths=[],
    )
    assert any("根目录 SKILL.md" in warning for warning in result.warnings)
    assert all(asset.path != "." for asset in result.assets)


# ---------------------------------------------------------------------------
# M8-10: CLI-generated assets, runtime prerequisites and load tests
# ---------------------------------------------------------------------------

INSTALL_COMMAND = "npm install -g @fission-ai/openspec"
INIT_COMMAND = "openspec init"
PREREQUISITES = {
    "install_command": INSTALL_COMMAND,
    "init_command": INIT_COMMAND,
}

GENERATED_FILES = {
    INIT_COMMAND: {
        ".claude/skills/openspec-explore/SKILL.md": (
            "---\nname: openspec-explore\ndescription: Explore OpenSpec changes\n---\n# Explore\n"
        ),
        ".claude/skills/openspec-explore/reference.md": "extra skill file\n",
        ".claude/commands/opsx/propose.md": "# propose\n",
        ".claude/commands/opsx/apply.md": "# apply\n",
        ".claude/agents/spec-reviewer.md": (
            "---\nname: spec-reviewer\ndescription: Review specs\n---\n# Reviewer\n"
        ),
    }
}


def _write_generated(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def install_fake_setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    files_by_command: dict[str, dict[str, str]] | None = None,
    returncode: int = 0,
    error: Exception | None = None,
    stdout: str = "setup ok\n",
    stderr: str = "",
) -> list[dict]:
    """Replace command execution; optionally materialize generated files in cwd."""

    calls: list[dict] = []
    files_by_command = files_by_command or {}

    def fake_setup_command(command: str, *, cwd: str, env: dict[str, str], timeout: int):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": dict(env),
                "timeout": timeout,
            }
        )
        if error is not None:
            raise error
        _write_generated(Path(cwd), files_by_command.get(command, {}))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(agent_assets, "run_setup_command", fake_setup_command)
    return calls


def load_test_preview(
    client: TestClient,
    *,
    scan_paths: list[str] | None = None,
    prerequisites: dict | None = None,
) -> dict:
    response = client.post(
        "/api/v1/agent-asset-sources/load-test-preview",
        json={
            "repo_url": "https://github.com/chenmr0/OpenSpec-cn",
            "requested_ref": "main-copy-ar",
            "scan_paths": scan_paths or ["."],
            "runtime_prerequisites": PREREQUISITES if prerequisites is None else prerequisites,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_generated_source(
    client: TestClient,
    *,
    prerequisites: dict | None = None,
    token: str | None = None,
    name: str = "openspec-cn",
) -> dict:
    overrides: dict[str, object] = {
        "name": name,
        "repo_url": "https://github.com/chenmr0/OpenSpec-cn",
        "default_ref": "main-copy-ar",
        "scan_paths": ["."],
        "selection_mode": "directory",
        "runtime_prerequisites": PREREQUISITES if prerequisites is None else prerequisites,
    }
    if token is not None:
        overrides["load_test_token"] = token
    return create_source(client, **overrides)


def test_runtime_prerequisites_are_validated(
    client: TestClient, seeded: Session, fake_git: FakeRepo
) -> None:
    response = client.post(
        "/api/v1/agent-asset-sources",
        json={
            "name": "bad-prerequisites",
            "repo_url": "https://github.com/chenmr0/OpenSpec-cn",
            "runtime_prerequisites": {"install_command": "  ", "init_command": "x init"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "runtime_prerequisites_invalid"

    response = client.post(
        "/api/v1/agent-asset-sources",
        json={
            "name": "missing-init",
            "repo_url": "https://github.com/chenmr0/OpenSpec-cn",
            "runtime_prerequisites": {"install_command": "npm i -g x"},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "runtime_prerequisites_incomplete"

    source = create_source(client, runtime_prerequisites=PREREQUISITES)
    assert source["runtime_prerequisites"] == PREREQUISITES
    # Prerequisites alone never make a source ready: a load test must pass.
    assert source["status"] == "requires_setup"
    assert source["load_test"] == {}


def test_load_test_preview_runs_isolated_commands_and_returns_cached_token(
    client: TestClient,
    seeded: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_git: FakeRepo,
) -> None:
    calls = install_fake_setup(
        monkeypatch, files_by_command=GENERATED_FILES, stdout="installed across lines\n"
    )
    preview = load_test_preview(client)

    assert preview["status"] == "passed"
    assert preview["install_command"] == INSTALL_COMMAND
    assert preview["init_command"] == INIT_COMMAND
    assert preview["generated"] == {"skill": 1, "command": 2, "subagent": 1}
    assert {(item["asset_type"], item["name"], item["path"]) for item in preview["assets"]} == {
        ("skill", "openspec-explore", ".claude/skills/openspec-explore"),
        ("command", "opsx/propose", ".claude/commands/opsx/propose.md"),
        ("command", "opsx/apply", ".claude/commands/opsx/apply.md"),
        ("subagent", "spec-reviewer", ".claude/agents/spec-reviewer.md"),
    }
    assert preview["log_tail"]
    assert preview["load_test_token"]
    # A preview never writes a source row.
    assert client.get("/api/v1/agent-asset-sources").json()["items"] == []

    assert [call["command"] for call in calls] == [INSTALL_COMMAND, INIT_COMMAND]
    assert calls[0]["cwd"] == calls[1]["cwd"]
    assert calls[0]["env"]["HOME"] == calls[0]["cwd"]
    assert calls[0]["env"]["PATH"]
    assert calls[0]["timeout"] == 600

    created = create_generated_source(client, token=preview["load_test_token"])
    assert created["status"] == "ready"
    assert created["load_test"]["status"] == "passed"
    assert created["load_test"]["generated"] == {"skill": 1, "command": 2, "subagent": 1}
    stored = seeded.get(AgentAssetSource, created["id"])
    assert stored is not None
    stored_result = stored.load_test_json
    assert stored_result["status"] == "passed"
    assert stored_result["install_command"] == INSTALL_COMMAND
    assert {item["asset_type"] for item in stored_result["generated"]} == {
        "skill",
        "command",
        "subagent",
    }
    skill = next(item for item in stored_result["generated"] if item["asset_type"] == "skill")
    assert {item["path"] for item in skill["files"]} == {
        ".claude/skills/openspec-explore/SKILL.md",
        ".claude/skills/openspec-explore/reference.md",
    }
    assert "# Explore" in skill["content"]


def test_create_without_valid_load_test_token_stays_requires_setup(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo
) -> None:
    # The preview itself must never invoke npm: this test only proves that a
    # cached token cannot be replayed against a different command pair.
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    preview = load_test_preview(client)
    # A different install command is a different configuration: the cached
    # result must not be replayed against it.
    created = create_generated_source(
        client,
        prerequisites={
            "install_command": "npm install -g another-cli",
            "init_command": "another-cli init",
        },
        token=preview["load_test_token"],
    )
    assert created["status"] == "requires_setup"
    assert created["load_test"] == {}

    expired = create_generated_source(
        client, name="openspec-cn-expired", token="not-a-cached-token"
    )
    assert expired["status"] == "requires_setup"
    assert expired["load_test"] == {}


def test_create_persists_a_failed_preview_result(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo
) -> None:
    install_fake_setup(monkeypatch, returncode=1, stderr="command not found\n")
    preview = load_test_preview(client)
    assert preview["status"] == "failed"
    created = create_generated_source(
        client, name="openspec-cn-failed", token=preview["load_test_token"]
    )
    assert created["status"] == "requires_setup"
    assert created["load_test"]["status"] == "failed"
    assert "command not found" in created["load_test"]["log_tail"]


def test_load_test_preview_requires_prerequisites(client: TestClient, seeded: Session) -> None:
    response = client.post(
        "/api/v1/agent-asset-sources/load-test-preview",
        json={
            "repo_url": "https://github.com/chenmr0/OpenSpec-cn",
            "requested_ref": "main-copy-ar",
            "scan_paths": ["."],
            "runtime_prerequisites": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "runtime_prerequisites_required"


def test_source_load_test_persists_result_and_returns_source(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo
) -> None:
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    source = create_generated_source(client)
    assert source["status"] == "requires_setup"

    response = client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"]["id"] == source["id"]
    assert body["source"]["status"] == "ready"
    assert body["source"]["load_test"]["status"] == "passed"
    assert body["load_test"]["generated"] == {"skill": 1, "command": 2, "subagent": 1}
    assert body["load_test"]["duration_seconds"] >= 0
    assert body["load_test"]["ran_at"]
    assert {item["path"] for item in body["load_test"]["assets"]} == {
        ".claude/skills/openspec-explore",
        ".claude/commands/opsx/propose.md",
        ".claude/commands/opsx/apply.md",
        ".claude/agents/spec-reviewer.md",
    }
    assert seeded.get(AgentAssetSource, source["id"]).status == "ready"


def test_failed_load_test_keeps_source_in_requires_setup(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo
) -> None:
    install_fake_setup(
        monkeypatch,
        returncode=2,
        stdout="npm output\n",
        stderr="EACCES: permission denied\n",
    )
    source = create_generated_source(client)
    response = client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["load_test"]["status"] == "failed"
    assert body["source"]["status"] == "requires_setup"
    assert "EACCES" in body["load_test"]["log_tail"]
    stored = seeded.get(AgentAssetSource, source["id"])
    assert stored.load_test_json["status"] == "failed"
    assert stored.status == "requires_setup"


def test_load_test_without_generated_assets_is_a_failure(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo
) -> None:
    install_fake_setup(monkeypatch, files_by_command={})
    source = create_generated_source(client)
    response = client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    body = response.json()
    assert body["load_test"]["status"] == "failed"
    assert body["source"]["status"] == "requires_setup"
    assert body["load_test"]["error"] == "no_generated_assets"


def test_load_test_infra_error_marks_source_error(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch, fake_git: FakeRepo
) -> None:
    install_fake_setup(monkeypatch, error=OSError("cannot spawn shell"))
    source = create_generated_source(client)
    response = client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    body = response.json()
    assert body["load_test"]["status"] == "failed"
    assert body["load_test"]["error"] == "load_test_execution_failed"
    assert body["source"]["status"] == "error"
    assert seeded.get(AgentAssetSource, source["id"]).status == "error"


def test_scan_keeps_requires_setup_until_load_test_passes(
    client: TestClient, seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_repo(monkeypatch, {})
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    source = create_generated_source(client)
    scanned = scan_source(client, source["id"])
    # The Git tree has no assets, but the configured CLI still needs setup.
    assert scanned["counts"] == {"skill": 0, "command": 0, "subagent": 0}
    refreshed = client.get(f"/api/v1/agent-asset-sources/{source['id']}").json()
    assert refreshed["status"] == "requires_setup"

    client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    scan_source(client, source["id"])
    refreshed = client.get(f"/api/v1/agent-asset-sources/{source['id']}").json()
    assert refreshed["status"] == "ready"


def _install_plain_repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    return _install_repo(
        monkeypatch,
        {
            "skills/plain/SKILL.md": (
                b"---\nname: plain\ndescription: Git-scanned skill\n---\n# Plain\n"
            ),
        },
    )


def test_tree_includes_generated_assets_and_resolve_freezes_selected_paths(
    client: TestClient,
    seeded: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    """The wizard tree must show CLI-generated assets, not just the Git scan."""

    _install_plain_repo(monkeypatch)
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    # No preview token: the source starts unverified and has no load-test
    # result yet, so the tree must stay Git-only until the test passes.
    source = create_generated_source(client)
    scan_source(client, source["id"])

    # Without a passed load test the tree keeps Git-only behaviour.
    before = client.get(f"/api/v1/agent-asset-sources/{source['id']}/tree").json()
    assert before["counts"] == {"skill": 1, "command": 0, "subagent": 0}
    assert [item["path"] for item in before["assets"]] == ["skills/plain"]
    assert all(not item["metadata"].get("generated") for item in before["assets"])

    client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    tree = client.get(f"/api/v1/agent-asset-sources/{source['id']}/tree").json()
    assert tree["scanned"] is True
    assert tree["counts"] == {"skill": 2, "command": 2, "subagent": 1}
    generated = {
        (item["asset_type"], item["path"]): item
        for item in tree["assets"]
        if item["metadata"].get("generated")
    }
    assert set(generated) == {
        ("skill", ".claude/skills/openspec-explore"),
        ("command", ".claude/commands/opsx/propose.md"),
        ("command", ".claude/commands/opsx/apply.md"),
        ("subagent", ".claude/agents/spec-reviewer.md"),
    }
    assert generated[("skill", ".claude/skills/openspec-explore")]["metadata"]["runtime_path"] == (
        "skills/openspec-explore"
    )
    # Git-scanned assets stay in the merged list.
    assert ("skill", "skills/plain") in {
        (item["asset_type"], item["path"]) for item in tree["assets"]
    }

    # Selecting the tree's generated paths must work through resolve and be
    # frozen into the snapshot/bundle.
    selected_paths = [
        item["path"] for item in tree["assets"] if item["metadata"].get("generated")
    ]
    snapshot = resolve(client, source["id"], selected_paths)["snapshot"]
    assert snapshot["asset_count"] == 4
    assert {
        (item["asset_type"], item["path"]) for item in snapshot["assets"]
    } >= set(generated)
    manifest = snapshot["manifest"]
    assert manifest["load_test"]["status"] == "passed"
    assert manifest["load_test"]["generated"] == {"skill": 1, "command": 2, "subagent": 1}
    assert manifest["runtime_setup"] == {
        "install_command": INSTALL_COMMAND,
        "init_command": INIT_COMMAND,
    }

    stored = seeded.get(AgentAssetSnapshot, snapshot["id"])
    assert stored is not None
    frozen = agent_assets.asset_snapshot_json(
        stored, agent_assets.snapshot_assets(seeded, stored.id)
    )
    assert frozen["runtime_setup"] == {
        "install_command": INSTALL_COMMAND,
        "init_command": INIT_COMMAND,
    }
    assert frozen["load_test"]["status"] == "passed"
    task_payload = agent_assets.task_asset_payload(frozen, get_settings())
    assert task_payload["runtime_setup"] == frozen["runtime_setup"]
    assert task_payload["bundle_url"].startswith("http://")


def test_run_setup_command_decodes_invalid_utf8_with_replacement(tmp_path: Path) -> None:
    completed = agent_assets.run_setup_command(
        "printf '\\377'",
        cwd=str(tmp_path),
        env={"HOME": str(tmp_path), "PATH": agent_assets.DEFAULT_SHELL_PATH},
        timeout=10,
    )
    assert completed.returncode == 0
    assert "\ufffd" in completed.stdout
    assert agent_assets._decode_command_output(b"caf\xe9") == "caf\ufffd"


def test_run_setup_command_timeout_kills_the_process_group(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        agent_assets.run_setup_command(
            "sleep 30",
            cwd=str(tmp_path),
            env={"HOME": str(tmp_path), "PATH": agent_assets.DEFAULT_SHELL_PATH},
            timeout=0.2,
        )
    # The 30s child must not keep the request (or its pipes) alive.
    assert time.monotonic() - started < 10


def test_resolve_merges_generated_assets_into_the_frozen_bundle(
    client: TestClient,
    seeded: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    _install_plain_repo(monkeypatch)
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    preview = load_test_preview(client)
    source = create_generated_source(
        client,
        prerequisites=PREREQUISITES,
        token=preview["load_test_token"],
    )
    scan_source(client, source["id"])

    resolved = resolve(client, source["id"], ["skills/plain"])
    snapshot = resolved["snapshot"]
    assert snapshot["asset_count"] == 5
    mapped = {(asset["asset_type"], asset["path"]): asset for asset in snapshot["assets"]}
    assert ("skill", "skills/plain") in mapped
    generated_skill = mapped[("skill", ".claude/skills/openspec-explore")]
    assert generated_skill["metadata"]["generated"] is True
    assert generated_skill["metadata"]["runtime_path"] == "skills/openspec-explore"
    assert generated_skill["metadata"]["file_count"] == 2
    assert ("command", ".claude/commands/opsx/propose.md") in mapped
    assert ("subagent", ".claude/agents/spec-reviewer.md") in mapped

    assert snapshot["manifest"]["load_test"]["status"] == "passed"
    assert snapshot["manifest"]["runtime_prerequisites"] == PREREQUISITES

    bundle = asset_store.bodies[f"/eval-artifacts/{snapshot['bundle_key']}"]
    document = json.loads(gzip.decompress(bundle))
    files = {item["path"]: item for item in document["files"]}
    assert files["skills/plain/SKILL.md"]
    assert files["skills/openspec-explore/SKILL.md"]
    assert files["skills/openspec-explore/reference.md"]
    assert files["commands/opsx/propose.md"]
    assert files["commands/opsx/apply.md"]
    assert files["agents/spec-reviewer.md"]
    assert snapshot["file_count"] == 6

    # Re-resolving the same commit, selection and load test is idempotent.
    again = resolve(client, source["id"], ["skills/plain"])
    assert again["created"] is False
    assert again["snapshot"]["id"] == snapshot["id"]


def test_resolve_generated_only_source_selects_generated_paths(
    client: TestClient,
    seeded: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    _install_repo(monkeypatch, {})
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    preview = load_test_preview(client)
    source = create_generated_source(client, token=preview["load_test_token"])
    assert source["status"] == "ready"
    scan_source(client, source["id"])

    resolved = resolve(client, source["id"], [".claude"])
    snapshot = resolved["snapshot"]
    assert snapshot["asset_count"] == 4
    assert {asset["asset_type"] for asset in snapshot["assets"]} == {
        "skill",
        "command",
        "subagent",
    }


def test_resolve_generated_asset_conflict_with_git_scan_fails_clearly(
    client: TestClient,
    seeded: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    _install_repo(
        monkeypatch,
        {".claude/commands/opsx/propose.md": b"# Git version\n"},
    )
    install_fake_setup(monkeypatch, files_by_command=GENERATED_FILES)
    preview = load_test_preview(client)
    source = create_generated_source(client, token=preview["load_test_token"])
    scan_source(client, source["id"])

    response = client.post(
        "/api/v1/agent-assets/resolve",
        json={
            "source_id": source["id"],
            "paths": [".claude/commands/opsx/propose.md"],
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "generated_asset_conflict"
    assert error["details"]["context"]["path"] == "commands/opsx/propose.md"


def test_source_load_test_requires_configured_prerequisites(
    client: TestClient, seeded: Session, fake_git: FakeRepo
) -> None:
    source = create_source(client, name="plain-source")
    response = client.post(f"/api/v1/agent-asset-sources/{source['id']}/load-test")
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "runtime_prerequisites_required"
