"""OSS-hosted dataset image tarballs for cold spot runners (issue #273).

A cold spot instance has no local Docker cache and the public mirrors are too
slow for multi-GB SWE-bench images. Operators publish a compressed
``docker save`` tarball to OSS once; this module derives the deterministic
object key and presigns a short-lived internal GET URL for each case. The
runner tries that tarball before falling back to the public registry chain.
"""

from __future__ import annotations

import hashlib

from ..config import Settings
from .object_store import object_store_from_settings

TARBALL_SUFFIX = ".tar.gz"
TARBALL_FORMAT = "tar.gz"


def tarball_key(image: str, *, prefix: str = "dataset-images/") -> str:
    """Deterministic OSS key for one image reference.

    The key depends only on the normalized image reference so the publish
    script and the task API cannot drift; ``docker save`` output is not
    deterministic, but the object key is.
    """

    normalized = (image or "").strip()
    if not normalized:
        raise ValueError("镜像引用不能为空")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    clean_prefix = prefix.strip("/")
    return f"{clean_prefix}/{digest}{TARBALL_SUFFIX}" if clean_prefix else f"{digest}{TARBALL_SUFFIX}"


def task_image_tarball(settings: Settings, image: str | None) -> dict | None:
    """Build the per-case task descriptor, or ``None`` when OSS is unconfigured.

    The presigned URL points at the internal Aliyun endpoint when available;
    it is harmless when the object has not been published yet because the
    runner treats a 404 as "fall back to the registry chain".
    """

    normalized = (image or "").strip()
    if not normalized:
        return None
    store = object_store_from_settings(settings, internal=True)
    if store is None:
        return None
    key = tarball_key(normalized, prefix=settings.dataset_image_tarball_prefix)
    ttl = max(60, int(settings.dataset_image_tarball_ttl_seconds))
    return {
        "url": store.presign_get_url(key, expires=ttl),
        "key": key,
        "image": normalized,
        "format": TARBALL_FORMAT,
    }
