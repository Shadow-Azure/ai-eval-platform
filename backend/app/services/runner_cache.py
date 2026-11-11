"""Cache the GitHub Actions runner tarball in OSS for spot cloud-init (M5-08).

Aliyun spot instances cannot reliably download the ~10 MB runner archive from
GitHub: the transfer often exceeds cloud-init's timeout, so whole batches of
instances fail to register. The deploy pipeline therefore ensures one stable
object exists in the platform bucket, and the api serves it through the fixed
``/runner/v1/runner-tarball/<version>.tar.gz`` endpoint (M5-11). That proxy URL
never expires, so a deploy gap no longer breaks new instances, while the bucket
stays private and downloads reuse the runner heartbeat token.

The key is version-stable (no per-run suffix) so repeated deploys reuse the
cached bytes, and a truncated/corrupt object is replaced instead of trusted.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable

from .object_store import ObjectMissing, ObjectStore, ObjectStoreError

#: Object key prefix shared with the spot-smoke integration test.
RUNNER_CACHE_PREFIX = "runner/actions-runner-linux-x64-"
#: A real runner archive is ~10 MB; anything smaller is a failed/partial upload.
MIN_RUNNER_BYTES = 10_000_000
#: Fixed api route that streams the cached archive without a presigned URL.
RUNNER_TARBALL_ROUTE = "/runner/v1/runner-tarball/"


def runner_cache_key(version: str) -> str:
    return f"{RUNNER_CACHE_PREFIX}{version}.tar.gz"


def runner_tarball_path(version: str) -> str:
    """Permanent api path for one cached archive version."""

    return f"{RUNNER_TARBALL_ROUTE}{version}.tar.gz"


def runner_tarball_url(base_url: str, version: str) -> str:
    """Absolute proxy URL derived from the platform's callback base URL."""

    return f"{base_url.rstrip('/')}{runner_tarball_path(version)}"


def github_runner_url(version: str) -> str:
    return (
        "https://github.com/actions/runner/releases/download/"
        f"v{version}/actions-runner-linux-x64-{version}.tar.gz"
    )


def download_runner(version: str, *, timeout: float = 300) -> bytes:
    with urllib.request.urlopen(github_runner_url(version), timeout=timeout) as response:
        return response.read()


def _valid_archive(payload: bytes) -> bool:
    return len(payload) >= MIN_RUNNER_BYTES and payload[:2] == b"\x1f\x8b"


def ensure_runner_cache(
    store: ObjectStore,
    *,
    version: str,
    downloader: Callable[[str], bytes] = download_runner,
) -> bool:
    """Ensure the version's archive exists; return whether it was uploaded.

    An existing object with a plausible size is reused; a missing or truncated
    object triggers one download from GitHub, which must be a valid gzip
    archive of at least ``MIN_RUNNER_BYTES`` before it is uploaded.
    """

    key = runner_cache_key(version)
    uploaded = False
    try:
        size = store.head_object(key)
        uploaded = size < MIN_RUNNER_BYTES
    except ObjectMissing:
        uploaded = True
    if uploaded:
        payload = downloader(version)
        if not _valid_archive(payload):
            raise ObjectStoreError(
                f"runner 包无效（期望 >= {MIN_RUNNER_BYTES} 字节的 gzip）：{len(payload)} 字节"
            )
        store.put_bytes(key, payload, content_type="application/gzip")
    return uploaded


__all__ = [
    "MIN_RUNNER_BYTES",
    "RUNNER_CACHE_PREFIX",
    "RUNNER_TARBALL_ROUTE",
    "ensure_runner_cache",
    "github_runner_url",
    "runner_cache_key",
    "runner_tarball_path",
    "runner_tarball_url",
]
