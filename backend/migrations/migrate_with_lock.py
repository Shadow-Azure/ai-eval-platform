"""Run Alembic under a PostgreSQL advisory lock (single-flight migration)."""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg

LOCK_KEY = 0x6D3202


def main() -> int:
    raw_url = os.environ["DATABASE_URL"]
    url = raw_url.replace("postgresql+psycopg://", "postgresql://")

    if url.startswith(("postgresql://", "postgres://")):
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            try:
                subprocess.run(
                    [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                    check=True,
                )
            finally:
                connection.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    else:
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            check=True,
        )
    print("migrations: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
