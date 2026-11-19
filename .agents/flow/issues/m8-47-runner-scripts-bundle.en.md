# [m8-47] Pre-seed the runner scripts bundle in the image/OSS to reduce spot startup dependency

English | [中文](m8-47-runner-scripts-bundle.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-45-queue-progress-lease.*
  - .agents/flow/issues/m8-47-runner-scripts-bundle.*
  - .github/workflows/deploy.yml
  - .github/workflows/eval-shard.yml
  - backend/app/api/runners.py
  - backend/app/config.py
  - backend/app/services/dispatcher.py
  - backend/app/services/provisioning/**
  - backend/app/services/runner_scripts_cache.py
  - docs/architecture.*
  - plans/2026-11-19-pr*-review.md
  - plans/m1-02-fullstack-design/**
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner/**
  - scripts/**
  - tests/**
adr: []
github:
  number: 312
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/312
```

## Background

In the #306 incident an execute job sat in `Fetch the scripts bundle`: the runner first used a short-lived token against the platform metadata endpoint and then downloaded through the platform proxy. With spot network jitter, the download retries stretched startup to more than ten minutes. PR #308 now reports `runner_bootstrap_failed` before started, but the bundle still depends on the platform endpoint and the job-time network.

M5-12 already provides the content-addressed bundle, the private OSS cache and the deterministic tar. This issue only changes where the workflow gets it and how it is pre-seeded; the bundle content, md5 naming and private OSS semantics stay unchanged.

## Goals

- Pre-seed the bundle to `/opt/eval/runner-scripts/<name>.tar.gz` before the spot instance takes a GitHub job: an image may bake the file directly, and cloud-init prefetches from a short-lived private OSS presigned URL and verifies sha256 for images without it; the workflow reads the image cache when present.
- Carry the shard-frozen `scripts_bundle` name, md5, sha256 and size in the dispatch payload so the workflow can find the image cache without first calling the platform scripts endpoint.
- Add a private OSS presigned GET URL to the platform scripts metadata; when the image cache is missing, the workflow prefers direct OSS, then the platform proxy, and keeps GitHub checkout only as the last fallback.
- Bound bundle acquisition with a short timeout (60 seconds by default) and settle a failure through #308's `runner_bootstrap_failed` instead of waiting for the queue timeout.
- Leave no long-lived platform credential on the instance; the presigned URL is a short-lived download capability and disappears with the cloud-init cleanup.
- Produce both the deterministic image-cache directory and the OSS object from the release step; document publishing and fallback order.

## Acceptance

- With the platform scripts endpoint unreachable, the spot runner still gets the bundle from the image cache and executes; without the image cache it downloads directly from the private OSS presigned URL.
- When neither image nor OSS is reachable, workflow bundle acquisition fails within 60 seconds and reports `runner_bootstrap_failed` before started; the platform creates the next attempt under the existing retry budget.
- Two builds of the same source produce the same md5/sha256; the pre-seeded file is named `eval-runner-scripts-<md5>.tar.gz` and checked against sha256.
- Instance and workflow logs contain no OSS secret, platform task token or long-lived credential.
- `npm test`, `pytest tests/backend`, `npm run verify:flow` and `npm run verify:docs` pass.
