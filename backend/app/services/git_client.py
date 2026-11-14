"""Minimal GitHub REST client for Agent asset sources (M5-01).

The platform reads a repository tree and selected blobs at a resolved commit;
it never clones the repository and never executes repository content. Public
repositories work without a token (subject to GitHub's anonymous rate limit);
a tenant Git credential or the platform ``GITHUB_TOKEN`` raises that limit and
enables private repositories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

GITHUB_API_URL = "https://api.github.com"
_REPO_PATTERNS = (
    re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?$"),
    re.compile(r"^github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?$"),
    re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"),
)


class GitClientError(Exception):
    """A Git provider call failed; ``code`` is stable for the API envelope."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        self.code = code
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class TreeEntry:
    path: str
    entry_type: str
    sha: str
    size: int | None = None
    mode: str = "100644"


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` for a GitHub URL or slug."""

    value = (repo_url or "").strip()
    for pattern in _REPO_PATTERNS:
        match = pattern.match(value)
        if match:
            owner = match.group("owner").strip()
            repo = match.group("repo").strip()
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            if owner and repo:
                return owner, repo
    raise GitClientError(
        "repo_provider_unsupported",
        "资产源只支持 GitHub 仓库（https://github.com/owner/repo）",
        status=422,
    )


class GitHubClient:
    """Small read-only GitHub API surface used by the asset scanner."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str = GITHUB_API_URL,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.token = (token or "").strip() or None
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-eval-platform-agent-assets",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._client.get(f"{self.api_url}{path}", headers=headers, params=params)
        except httpx.HTTPError as error:
            raise GitClientError(
                "git_unreachable", f"GitHub API 不可达：{type(error).__name__}", status=502
            ) from error
        if response.status_code == 401:
            raise GitClientError("git_auth_failed", "GitHub 凭据无效或已过期", status=401)
        if response.status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                raise GitClientError(
                    "git_rate_limited", "GitHub API 速率限制已用尽，请配置访问令牌", status=429
                )
            raise GitClientError("git_forbidden", "GitHub 拒绝访问该仓库", status=403)
        if response.status_code == 404:
            raise GitClientError("repo_not_found", "仓库或 Ref 不存在", status=404)
        if response.status_code >= 400:
            raise GitClientError(
                "git_request_failed",
                f"GitHub API 返回 HTTP {response.status_code}",
                status=502,
            )
        try:
            return response.json()
        except ValueError as error:
            raise GitClientError("git_response_invalid", "GitHub API 响应不是 JSON", status=502) from error

    def resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        payload = self._get(f"/repos/{owner}/{repo}/commits/{quote(ref, safe='')}")
        sha = payload.get("sha") if isinstance(payload, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitClientError("ref_unresolved", f"无法解析 Ref {ref}", status=422)
        return sha

    def tree(self, owner: str, repo: str, commit: str) -> list[TreeEntry]:
        payload = self._get(
            f"/repos/{owner}/{repo}/git/trees/{quote(commit, safe='')}",
            params={"recursive": "1"},
        )
        if not isinstance(payload, dict):
            raise GitClientError("git_response_invalid", "GitHub tree 响应无效", status=502)
        if payload.get("truncated"):
            raise GitClientError(
                "repo_tree_truncated",
                "仓库文件树过大，GitHub 返回了截断结果；请缩小扫描路径",
                status=422,
            )
        entries = payload.get("tree")
        if not isinstance(entries, list):
            raise GitClientError("git_response_invalid", "GitHub tree 缺少条目", status=502)
        result: list[TreeEntry] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            entry_type = entry.get("type")
            sha = entry.get("sha")
            if not isinstance(path, str) or not isinstance(sha, str):
                continue
            if entry_type not in ("blob", "tree"):
                continue
            size = entry.get("size")
            mode = entry.get("mode")
            result.append(
                TreeEntry(
                    path=path,
                    entry_type=entry_type,
                    sha=sha,
                    size=size if isinstance(size, int) else None,
                    mode=mode if isinstance(mode, str) else "100644",
                )
            )
        return result

    def archive(
        self, owner: str, repo: str, commit: str, *, max_bytes: int = 128 * 1024 * 1024
    ) -> bytes:
        """Download the source tree at ``commit`` as a gzipped tarball.

        M8-22: CLI-generated assets are built from the asset repository itself,
        so the platform fetches the resolved commit once and hands the runner a
        short-lived archive URL. The runner never receives a Git credential and
        never needs Git on the host; extracting the archive at the pinned
        commit is equivalent to ``git clone`` + ``git checkout <commit>``.
        """

        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-eval-platform-agent-assets",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        path = f"/repos/{owner}/{repo}/tarball/{quote(commit, safe='')}"
        try:
            response = self._client.get(
                f"{self.api_url}{path}", headers=headers, follow_redirects=True
            )
        except httpx.HTTPError as error:
            raise GitClientError(
                "git_unreachable", f"GitHub API 不可达：{type(error).__name__}", status=502
            ) from error
        if response.status_code == 401:
            raise GitClientError("git_auth_failed", "GitHub 凭据无效或已过期", status=401)
        if response.status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                raise GitClientError(
                    "git_rate_limited", "GitHub API 速率限制已用尽，请配置访问令牌", status=429
                )
            raise GitClientError("git_forbidden", "GitHub 拒绝访问该仓库", status=403)
        if response.status_code == 404:
            raise GitClientError("repo_not_found", "仓库或 Ref 不存在", status=404)
        if response.status_code >= 400:
            raise GitClientError(
                "git_request_failed",
                f"GitHub API 返回 HTTP {response.status_code}",
                status=502,
            )
        content = response.content
        if not content:
            raise GitClientError("source_archive_empty", "GitHub 返回了空的源码包", status=502)
        if len(content) > max_bytes:
            raise GitClientError(
                "source_archive_too_large",
                f"源码包大小 {len(content)} 超过上限 {max_bytes}",
                status=422,
            )
        return content

    def blob(self, owner: str, repo: str, sha: str) -> bytes:
        payload = self._get(
            f"/repos/{owner}/{repo}/git/blobs/{quote(sha, safe='')}",
        )
        if not isinstance(payload, dict) or payload.get("encoding") != "base64":
            raise GitClientError("git_response_invalid", "GitHub blob 响应无效", status=502)
        import base64
        import binascii

        content = payload.get("content")
        if not isinstance(content, str):
            raise GitClientError("git_response_invalid", "GitHub blob 缺少内容", status=502)
        try:
            return base64.b64decode(content, validate=False)
        except (binascii.Error, ValueError) as error:
            raise GitClientError("git_response_invalid", "GitHub blob 解码失败", status=502) from error
