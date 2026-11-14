# [m8-25] Dataset image public-source check: probe at creation, warn and block when missing

English | [中文](m8-25-dataset-image-registry-check.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-25-dataset-image-registry-check.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/config.py
  - backend/app/api/datasets.py
  - backend/app/api/runners.py
  - backend/app/models/dataset.py
  - backend/app/services/dataset_images.py
  - backend/app/services/dataset_imports.py
  - backend/app/services/datasets.py
  - backend/app/services/evaluations.py
  - backend/migrations/versions/0024_dataset_case_environment_image.py
  - runner/lib/task-payload.mjs
  - tests/backend/conftest.py
  - tests/backend/test_dataset_images.py
  - tests/backend/test_dataset_import_upload.py
  - tests/backend/test_datasets.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_migrations.py
  - tests/backend/test_operations.py
  - tests/runner/task-payload.test.mjs
  - web/src/api/datasets.ts
  - web/src/pages/DatasetImportPage.tsx
  - web/src/styles/datasets.css
  - prototype/**
  - plans/m1-02-fullstack-design/05-data-model.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - plans/2026-11-14-pr235-review.md
adr: []
github:
  number: 234
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/234
```

## Background

The upstream SWE-bench dataset does not carry `environment_image`; the evaluation harness derives the image name per instance and architecture as `swebench/sweb.eval.x86_64.<org>_1776_<repo>-<issue>:latest`. The platform previously stored one private ACR address on `dataset_version.environment_image`, never checked pullability and had no public fallback; after the runner cache was pruned, evaluation failed only at `docker create`.

## Goal

- Configure ordered public registry sources (`DATASET_IMAGE_REGISTRIES`, Docker Hub by default; domestic mirrors, GHCR or public ACR repos can be appended).
- SWE-bench derives one image per case from `instance_id`, with explicit `docker_image`/`image_name` overrides; persist it on `dataset_case.environment_image`.
- Probe at dataset import/version creation in source order; when no source serves an image, persist `image_check` and block version creation (both upload validation and legacy JSON import).
- The import wizard Step 2 shows per-source results, per-case images and a blocking alert; “Generate version” stays disabled until every image is available.
- The runner receives a per-case image; the version-level image remains a fallback for single-image datasets.

## Acceptance

- With multiple public sources, probing follows the configured order; one hit passes, while all 404/401/timeout outcomes block and write no partial version.
- Every DatasetCase has a nullable `environment_image`; the runner receives the per-case image.
- Upload and legacy imports are both blocked server-side; commit refuses an import whose image check is blocked.
- The frontend shows sources, cases and counts inline, and disables the action when blocked; screenshots match issue #234.
- Unit tests cover derivation, explicit override, first/later source hits, all 404, 401, timeout, no-source skip and commit blocking; existing dataset import tests still pass.
