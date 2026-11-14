# [m8-26] Editable public image sources in the console; deploy defaults from GitHub Secrets

English | [中文](m8-26-dataset-image-sources-settings.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-26-dataset-image-sources-settings.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/api/dataset_image_sources.py
  - backend/app/main.py
  - backend/app/models/__init__.py
  - backend/app/models/dataset.py
  - backend/app/services/dataset_images.py
  - backend/app/services/dataset_imports.py
  - backend/app/services/datasets.py
  - backend/migrations/versions/0025_dataset_image_sources.py
  - deploy/deploy.sh
  - deploy/docker-compose.prod.yml
  - deploy/write-managed-env.sh
  - .github/workflows/deploy.yml
  - prototype/**
  - web/src/api/datasetImageSources.ts
  - web/src/app/nav.ts
  - web/src/app/routes.tsx
  - web/src/pages/DatasetImageSourcesPage.tsx
  - web/src/pages/DatasetImportPage.tsx
  - web/src/styles/datasets.css
  - tests/backend/conftest.py
  - tests/backend/test_dataset_image_sources_settings.py
  - tests/backend/test_datasets.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_operations.py
  - tests/deploy/**
  - plans/m1-02-fullstack-design/05-data-model.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - plans/2026-11-14-pr236-review.md
adr: []
github:
  number: 236
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/236
```

## Background

m8-25 stored the public image sources only in the backend `DATASET_IMAGE_REGISTRIES` environment variable and exposed a read-only list in the import wizard. Changing sources in production required editing the host compose/`.env`, which is not reproducible and is overwritten by the next deploy. Defaults should come from GitHub Secrets and be written into the backend `.env` at deploy time, while administrators can edit the current-environment override in the console.

## Goal

- Add platform settings storage and `GET/PUT/DELETE /api/v1/settings/dataset-image-sources`; GET returns deploy defaults, DB override and effective list; PUT saves an ordered override; DELETE restores defaults.
- Add a console “dataset image sources” settings page: reorder, enable/disable, add/remove, edit name/registry/namespace, restore defaults and save; link it from the import wizard.
- Probing prefers the DB override and falls back to `Settings.dataset_image_registries`; upload worker and legacy import use the effective list.
- GitHub Secrets provides `DATASET_IMAGE_REGISTRIES`; the deploy workflow writes it through `write-managed-env.sh` into the backend `.env`, and compose injects it into api/worker. No manual host `.env` edit.
- Preserve m8-25 behavior when no override exists.

## Acceptance

- GET without override returns env defaults; PUT then GET returns the new order; DELETE falls back to env.
- Upload/legacy imports use the DB override order; saving applies to the next validation.
- The settings page saves and validates; invalid/duplicate/empty sources return 4xx; non-admin cannot write.
- Deploy injects the secret; containers and `.env` agree after deploy; no manual `.env` edit.
- Migration, backend tests, web typecheck/build and deploy contract tests pass; docs/flow updated.

## Out of scope

- Building/pushing images, private registry auth, runner pull strategy, image mirroring.
