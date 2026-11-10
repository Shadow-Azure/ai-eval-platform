"""One-time GitHub Actions runner registration (M4-04, design 06 §6.2).

The spot provisioner creates a runner *before* the cloud instance boots:
GitHub's JIT config endpoint returns a single-use encoded registration
blob that the instance consumes through cloud-init. The runner is bound to
the repository, a unique name and the pool's labels, and enters ephemeral
mode, so it takes at most one job and removes itself.

Secrets policy:

* the encoded JIT config only exists in the cloud-init user data handed to
  the provider; it is never persisted on ``ProvisionedInstance`` rows,
  resource events, logs or API responses;
* the admin token comes from ``RUNNER_ADMIN_TOKEN`` (a repository admin PAT
  or GitHub App installation token), never from the dispatch token;
* errors carry a stable code and no token/ciphertext.

The HTTP transport is injectable so the test suite never touches GitHub.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Settings
from .provisioning import ProvisionerError

RunnerTransport = Callable[..., tuple[int, str]]


@dataclass(frozen=True)
class JitRunner:
    """Decoded JIT registration material (``encoded`` is a secret)."""

    runner_id: int | None
    name: str
    encoded: str
    labels: tuple[str, ...]


def _default_transport(
    url: str, *, method: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    # urllib.error.URLError/OSError bubble up to the caller.


class GitHubRunnerRegistrar:
    """Repository-scoped JIT runner lifecycle through the GitHub REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: RunnerTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.settings = settings
        self._transport = transport or _default_transport
        self.timeout = timeout

    # -- HTTP ---------------------------------------------------------------

    @property
    def _base(self) -> str:
        return self.settings.github_api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.runner_admin_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "ai-eval-platform-provisioner",
        }

    def _require_token(self) -> None:
        if not self.settings.runner_admin_token:
            raise ProvisionerError(
                "runner_registration_unavailable",
                "RUNNER_ADMIN_TOKEN 未配置，无法生成一次性 Runner 注册配置",
            )

    def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, str]:
        self._require_token()
        url = f"{self._base}{path}"
        body = b"" if payload is None else json.dumps(payload).encode()
        try:
            return self._transport(
                url, method=method, body=body, headers=self._headers(), timeout=self.timeout
            )
        except Exception as exc:  # network/DNS errors are retryable provider failures
            raise ProvisionerError(
                "runner_registration_failed",
                f"GitHub Runner 注册请求传输失败：{type(exc).__name__}",
            ) from exc

    @staticmethod
    def _error(status: int, body: str) -> ProvisionerError:
        # GitHub's error text is safe (no credentials), but keep it short and
        # strip the response body from the message to avoid echoing headers.
        if status in (401, 403):
            return ProvisionerError(
                "runner_registration_forbidden",
                "RUNNER_ADMIN_TOKEN 没有仓库 Runner 管理权限",
                details={"status": status},
            )
        if status == 404:
            return ProvisionerError(
                "runner_repository_not_found",
                "GitHub 仓库不存在或 Token 无权访问",
                details={"status": status},
            )
        details: dict[str, object] = {"status": status}
        # 422 is GitHub's validation response (e.g. a reserved label); its body
        # contains field explanations, never the returned JIT blob.
        if status == 422:
            details["body"] = body.strip()[:500]
        return ProvisionerError(
            "runner_registration_failed",
            f"GitHub Runner 注册失败：HTTP {status}",
            details=details,
        )

    # -- API ----------------------------------------------------------------

    def generate_jitconfig(
        self,
        *,
        name: str,
        labels: list[str],
        runner_group_id: int | None = 1,
    ) -> JitRunner:
        """Create a single-use ephemeral runner registration for ``name``."""

        # GitHub's generate-jitconfig endpoint requires runner_group_id even
        # for the repository default group (1); omitting it returns 422.
        payload: dict[str, object] = {
            "name": name,
            "labels": list(labels),
            "runner_group_id": runner_group_id if runner_group_id is not None else 1,
        }
        status, text = self._request(
            "POST",
            f"/repos/{self.settings.github_repo}/actions/runners/generate-jitconfig",
            payload,
        )
        if status not in (200, 201):
            raise self._error(status, text)
        try:
            body = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise ProvisionerError(
                "runner_registration_failed", "GitHub Runner 注册返回非 JSON"
            ) from exc
        encoded = body.get("encoded_jit_config")
        runner = body.get("runner") if isinstance(body.get("runner"), dict) else {}
        if not isinstance(encoded, str) or not encoded:
            raise ProvisionerError(
                "runner_registration_failed", "GitHub Runner 注册响应缺少 JIT 配置"
            )
        runner_id = runner.get("id") if isinstance(runner.get("id"), int) else None
        return JitRunner(
            runner_id=runner_id,
            name=str(runner.get("name") or name),
            encoded=encoded,
            labels=tuple(str(item) for item in labels),
        )

    def find_runner(self, name: str) -> dict | None:
        """Return the repository runner record named ``name``, or ``None``."""

        for page in range(1, 6):
            status, text = self._request(
                "GET",
                f"/repos/{self.settings.github_repo}/actions/runners"
                f"?per_page=100&page={page}",
            )
            if status != 200:
                raise self._error(status, text)
            try:
                payload = json.loads(text or "{}")
            except json.JSONDecodeError as exc:
                raise ProvisionerError(
                    "runner_registration_failed", "GitHub Runner 列表返回非 JSON"
                ) from exc
            runners = payload.get("runners") or []
            for item in runners:
                if isinstance(item, dict) and item.get("name") == name:
                    return item
            if len(runners) < 100:
                break
        return None

    def delete_runner(
        self,
        *,
        runner_id: int | None = None,
        name: str | None = None,
    ) -> bool:
        """Delete a runner by numeric id or unique name.

        A 404 means the ephemeral runner already removed itself after its
        job; that is success for the release path.
        """

        if runner_id is None and name is not None:
            record = self.find_runner(name)
            if record is None:
                return False
            candidate = record.get("id")
            if isinstance(candidate, int):
                runner_id = candidate
        if runner_id is None:
            return False
        status, text = self._request(
            "DELETE", f"/repos/{self.settings.github_repo}/actions/runners/{runner_id}"
        )
        if status in (200, 204, 404):
            return status != 404
        raise self._error(status, text)
