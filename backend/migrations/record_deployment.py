"""Insert one deployment record using the API image's runtime dependencies."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb


def main() -> int:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    status = os.environ.get("DEPLOY_STATUS", "deploying")
    finished = (
        datetime.now(UTC).isoformat()
        if status in {"succeeded", "failed", "rolled_back"}
        else None
    )
    record = {
        "id": str(uuid.uuid4()),
        "environment": os.environ.get("DEPLOY_ENVIRONMENT", "production"),
        "git_commit": os.environ.get("GIT_COMMIT", "unknown"),
        "image_digest": os.environ.get("DEPLOY_IMAGE_DIGEST", "unknown"),
        "migration_revision": os.environ.get("DEPLOY_MIGRATION_REVISION", "0002_deployment"),
        "status": status,
        "triggered_by": os.environ.get("DEPLOY_TRIGGERED_BY"),
        "started_at": os.environ.get("DEPLOY_STARTED_AT"),
        "finished_at": finished,
        "rollback_of_id": os.environ.get("DEPLOY_ROLLBACK_OF_ID") or None,
        "detail_json": Jsonb(
            {
                "api_image": os.environ.get("DEPLOY_DETAIL_API_IMAGE", ""),
                "web_image": os.environ.get("DEPLOY_DETAIL_WEB_IMAGE", ""),
                "git_commit": os.environ.get("DEPLOY_DETAIL_GIT_COMMIT", ""),
            }
        ),
    }
    try:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO deployment (
                id, environment, git_commit, image_digest, migration_revision,
                status, triggered_by, started_at, finished_at, rollback_of_id, detail_json
            ) VALUES (
                %(id)s, %(environment)s, %(git_commit)s, %(image_digest)s,
                %(migration_revision)s, %(status)s, %(triggered_by)s,
                COALESCE(%(started_at)s::timestamptz, now()), %(finished_at)s::timestamptz,
                    %(rollback_of_id)s, %(detail_json)s
                )
                """,
                record,
            )
    except psycopg.errors.UndefinedTable:
        print(json.dumps({"status": "skipped", "reason": "deployment table missing"}))
        return 0
    print(json.dumps({"status": "recorded", "id": record["id"], "deployment_status": status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
