# [m8-33] Resolve and fall back across image sources at evaluation runtime

English | [中文](m8-33-runtime-image-fallback.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-33-runtime-image-fallback.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/services/evaluations.py
  - backend/app/api/runners.py
  - runner-contract/schemas/v1/task-manifest.schema.json
  - runner/lib/task-payload.mjs
  - runner/lib/image-sources.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/**
  - tests/runner/**
  - plans/2026-11-15-pr251-review.md
  - tests/runner/image-sources.test.mjs
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - .github/workflows/backend-ci.yml
  - pyproject.toml
adr: []
github:
  number: 250
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/250
```

## Background

`DATASET_IMAGE_REGISTRIES` is only used for import probing; runtime does `docker create swebench/...` and only hits Docker Hub. On cold spot instances with Docker Hub 443 timeouts, two cases failed `runtime_error`, the rest queued, 0 succeeded. The configured sources were never tried at runtime.

## Goal

- Freeze effective `image_sources` in manifest/task payload.
- On local miss, build registry/prefix refs in source order and pull; missing/timeout/slow/failure falls through.
- Record selected source and attempts in trace/diagnostics; all-fail is `image_unavailable`.
- Public sources only; preserve old manifests and fixed-runner cache.

## Acceptance

- On spot, a Docker Hub timeout falls through and pulls from the next source.
- Runner/backend tests and CI pass; docs/flow synced.
