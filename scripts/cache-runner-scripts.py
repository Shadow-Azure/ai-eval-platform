#!/usr/bin/env python3
"""Build and cache the content-addressed runner-scripts bundle (M5-12).

The deploy pipeline runs this after checkout. It derives the file list and
modes from the git index, writes a deterministic tar.gz, and ensures the
object exists in the private OSS bucket before the api serves it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.services.object_store import object_store_from_settings
from app.services.runner_scripts_cache import (
    build_scripts_bundle,
    ensure_runner_scripts_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="checked-out repository root")
    parser.add_argument(
        "--github-output",
        default=None,
        help="append name/md5/sha256/size to this GitHub Actions output file",
    )
    args = parser.parse_args()

    artifact = build_scripts_bundle(Path(args.repo_root).resolve())
    store = object_store_from_settings(get_settings())
    if store is None:
        print(
            "object storage is not configured (S3_ENDPOINT/S3_BUCKET/keys)",
            file=sys.stderr,
        )
        return 1
    uploaded = ensure_runner_scripts_bundle(store, name=artifact.name, payload=artifact.payload)
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as output:
            output.write(f"name={artifact.name}\n")
            output.write(f"md5={artifact.md5}\n")
            output.write(f"sha256={artifact.sha256}\n")
            output.write(f"size={artifact.size}\n")
    print(
        f"runner scripts bundle: {'uploaded' if uploaded else 'hit'} "
        f"name={artifact.name} md5={artifact.md5} size={artifact.size}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
