"""M5-01 Agent asset sources, immutable snapshots and eval freezing.

The GitHub client is faked at the service boundary: these tests assert the
platform's scan/selection/packaging contract, not GitHub's HTTP behaviour.
Snapshot bundles use the local S3 stub so the real presigned PUT/GET path runs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest
from app.config import get_settings
from app.models import AgentAssetSnapshot, Credential, EvalRun
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
