#!/usr/bin/env python3
"""Ensure the actions-runner tarball is cached in OSS (deploy pipeline).

Reads the same S3 settings as the API, checks the version-stable object, and
uploads it from GitHub only when missing or truncated. The api serves the
object through its permanent proxy endpoint (M5-11), so this script only keeps
the cache warm and prints no credential-bearing URL to the workflow log.
"""

from __future__ import annotations

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
    uploaded = ensure_runner_cache(store, version=version)
    print(f"runner cache: {'uploaded' if uploaded else 'hit'} version={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
