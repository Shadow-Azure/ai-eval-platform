#!/usr/bin/env python3
"""Ensure the actions-runner tarball is cached in OSS (deploy pipeline).

Reads the same S3 settings as the API, checks the version-stable object, and
uploads it from GitHub only when missing or truncated. Writes the presigned
download URL to ``GITHUB_OUTPUT`` as ``url`` without printing the credential to
the (public) workflow log.
"""

from __future__ import annotations

import os
import sys

from app.config import get_settings
from app.services.object_store import object_store_from_settings
from app.services.runner_cache import ensure_runner_cache


def main() -> int:
    settings = get_settings()
    store = object_store_from_settings(settings)
    if store is None:
        print("object storage is not configured (S3_ENDPOINT/S3_BUCKET/keys)", file=sys.stderr)
        return 1
    version = settings.runner_spot_runner_version
    url, uploaded = ensure_runner_cache(store, version=version)
    print(f"runner cache: {'uploaded' if uploaded else 'hit'} version={version}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"url={url}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
