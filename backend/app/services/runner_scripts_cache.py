"""Content-addressed runner-scripts bundle in OSS (M5-12).

The eval-shard workflow needs the platform's runner scripts in every prepare
and execute job. Instead of cloning the whole platform repository, the deploy
pipeline builds one deterministic archive named by its md5, caches it under
``runner/scripts/`` and the api serves it through a permanent proxy endpoint.

The name is the cache key: identical bytes reuse the same object, a truncated
object is replaced, and the Docker/runner bundle never depends on a mutable
"latest" pointer.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .object_store import ObjectMissing, ObjectStore, ObjectStoreError

#: Object prefix shared with the deploy cache job and the scripts proxy.
RUNNER_SCRIPTS_CACHE_PREFIX = "runner/scripts/"
#: Permanent api route; the filename is the content address.
RUNNER_SCRIPTS_ROUTE = "/runner/v1/scripts/"
#: A real bundle is ~40 KB; anything smaller is a failed/partial upload.
MIN_RUNNER_SCRIPTS_BYTES = 10_000
RUNNER_SCRIPTS_NAME_RE = re.compile(
    r"^eval-runner-scripts-[0-9a-f]{32}\.tar\.gz$"
)
BUNDLE_ROOTS = ("runner", "runner-contract/schemas")


@dataclass(frozen=True)
class BundleEntry:
    """One tracked file with the mode recorded in the git index."""

    path: str
    mode: int
    data: bytes


@dataclass(frozen=True)
class BundleArtifact:
    name: str
    md5: str
    sha256: str
    size: int
    payload: bytes


def collect_bundle_entries(repo_root: Path) -> list[BundleEntry]:
    """Collect tracked bundle files and modes from the git index.

    Reading modes from git (instead of the filesystem/umask) is what makes the
    archive reproducible on a developer Mac and the Linux deploy runner.
    """

    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--stage", "--", *BUNDLE_ROOTS],
        check=True,
        text=True,
        capture_output=True,
    )
    entries: list[BundleEntry] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        mode_text = meta.split()[0]
        mode = int(mode_text, 8)
        if mode & 0o170000 != 0o100000:  # regular files only
            continue
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        entries.append(
            BundleEntry(
                path=path,
                mode=0o755 if mode & 0o111 else 0o644,
                data=(repo_root / path).read_bytes(),
            )
        )
    entries.sort(key=lambda entry: entry.path)
    return entries


def build_scripts_bundle(
    repo_root: Path,
    *,
    entries: list[BundleEntry] | None = None,
) -> BundleArtifact:
    """Build the deterministic gzip tar consumed by prepare/execute.

    Fixed order, mtime, owner and modes plus a timestamp-free gzip header make
    an unchanged tree produce the same md5, so the OSS cache key is stable.
    """

    bundle_entries = entries if entries is not None else collect_bundle_entries(repo_root)
    raw = io.BytesIO()
    with tarfile.open(
        fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT
    ) as archive:
        for entry in sorted(bundle_entries, key=lambda item: item.path):
            path = entry.path
            if path.startswith("/") or ".." in Path(path).parts:
                raise ObjectStoreError(f"脚本包路径非法：{path}")
            info = tarfile.TarInfo(path)
            info.size = len(entry.data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o755 if entry.mode & 0o111 else 0o644
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(entry.data))
    payload = gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)
    md5 = hashlib.md5(payload).hexdigest()
    sha256 = hashlib.sha256(payload).hexdigest()
    return BundleArtifact(
        name=f"eval-runner-scripts-{md5}.tar.gz",
        md5=md5,
        sha256=sha256,
        size=len(payload),
        payload=payload,
    )


def valid_runner_scripts_name(name: str) -> bool:
    return RUNNER_SCRIPTS_NAME_RE.fullmatch(name) is not None


def runner_scripts_key(name: str) -> str:
    if not valid_runner_scripts_name(name):
        raise ValueError(f"非法 runner scripts 包名：{name}")
    return f"{RUNNER_SCRIPTS_CACHE_PREFIX}{name}"


def runner_scripts_path(name: str) -> str:
    if not valid_runner_scripts_name(name):
        raise ValueError(f"非法 runner scripts 包名：{name}")
    return f"{RUNNER_SCRIPTS_ROUTE}{name}"


def runner_scripts_url(base_url: str, name: str) -> str:
    """Absolute proxy URL derived from the platform callback base URL."""

    return f"{base_url.rstrip('/')}{runner_scripts_path(name)}"


def _valid_bundle(payload: bytes) -> bool:
    return len(payload) >= MIN_RUNNER_SCRIPTS_BYTES and payload[:2] == b"\x1f\x8b"


def ensure_runner_scripts_bundle(
    store: ObjectStore,
    *,
    name: str,
    payload: bytes,
) -> bool:
    """Ensure one content-addressed bundle exists; return whether it uploaded.

    A same-sized existing object is reused. A missing, truncated or
    size-mismatched object is replaced; the caller has already computed the
    md5/sha256 and will publish only this immutable name.
    """

    if not valid_runner_scripts_name(name):
        raise ObjectStoreError(f"非法 runner scripts 包名：{name}")
    if not _valid_bundle(payload):
        raise ObjectStoreError(
            f"runner scripts 包无效（期望 >= {MIN_RUNNER_SCRIPTS_BYTES} 字节的 gzip）："
            f"{len(payload)} 字节"
        )
    key = runner_scripts_key(name)
    needs_upload = False
    try:
        size = store.head_object(key)
        needs_upload = size != len(payload)
    except ObjectMissing:
        needs_upload = True
    if needs_upload:
        store.put_bytes(key, payload, content_type="application/gzip")
        stored = store.head_object(key)
        if stored != len(payload):
            raise ObjectStoreError(
                f"runner scripts 包上传后大小不一致：远端 {stored}，本地 {len(payload)}"
            )
    return needs_upload


__all__ = [
    "MIN_RUNNER_SCRIPTS_BYTES",
    "RUNNER_SCRIPTS_CACHE_PREFIX",
    "RUNNER_SCRIPTS_NAME_RE",
    "RUNNER_SCRIPTS_ROUTE",
    "ensure_runner_scripts_bundle",
    "runner_scripts_key",
    "runner_scripts_path",
    "runner_scripts_url",
    "valid_runner_scripts_name",
]
