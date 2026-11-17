# Spot cold-start image pull failures: OSS image tarball distribution and failure-code fix

English | [中文](m8-spot-oss-image-tarball.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - scripts/**
  - docs/**
  - deploy/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 273
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/273
```

## Background

- A cold spot instance in the production contention pool has no local image and must pull the 4.16GB SWE-bench image from public mirrors within a 180s per-source and 300s total budget. Live runs never finish in 300s: the first two sources time out, the third is skipped as budget-exhausted, and all four attempts end with `image_unavailable`; the case never actually executes.
- The fixed pool passes because the fixed host already caches the image (four cached copies of the same image), but its 40GB system disk has only 4.3GB free and cannot host a registry cache; the platform therefore needs a central distribution channel that uses no local disk.
- After the pull failure the runner POSTs a failure with `error_code=image_unavailable`, but `failure.schema.json` and the backend allowlist reject that code; when the runner exits the platform misclassifies the attempt as `spot_interrupted` and retries it, so the final bad case reason says the spot instances were reclaimed and masks the real image-pull cause.
- The existing OSS bucket and the spot instances are both in cn-hangzhou and the VPC can reach the internal endpoint; the platform already has S3-compatible presign support, so it can host compressed image tarballs directly with on-demand downloads and no local disk.

## Goals

- Add OSS image tarball distribution: derive a deterministic key from the image reference and store a compressed `docker save` tarball in OSS; the runner task API returns a short-lived presigned internal GET URL, key and image reference per case.
- Make the runner try the OSS tarball first: download, `docker load` and verify the image before normal execution; on 404, timeout, corrupt tar or load failure fall back to the existing public-source pull and record diagnostics.
- Fix the failure-code path: add `image_unavailable` to the failure contract and backend classification, settle the case as a deterministic failure instead of retrying it as `spot_interrupted`, and keep the real cause in the case and bad case reason.
- Provide publishing tooling and documentation: export, compress and upload the image tarball on the fixed host with the same key derivation as the backend; add no database migration, derive the internal endpoint from the existing S3 endpoint and allow an explicit override.
- Keep the fixed pool and existing public-source path unchanged; without OSS configuration or a tarball object the runner behaves exactly as today.

## Acceptance

- Backend: `/runner/v1/tasks` returns `image_tarball` for each case when OSS is configured (internal presigned URL, deterministic key, image reference); the field is `null` when OSS is unconfigured or the endpoint is not Aliyun; key derivation has unit tests.
- Runner: with `image_tarball` present it downloads, loads and verifies first; success skips public pulls; 404, timeout or load failure falls back to public sources with diagnostics; the temporary tar is removed; new unit tests cover success, fallback and cleanup.
- Failure code: `failure.schema.json` accepts `image_unavailable`; the backend classifies it as a deterministic failure and does not enter spot retry; the failed case and bad case error/reason reflect the unavailable image.
- Publishing: a new host script and manage subcommand export, compress, upload and verify with the same key; documentation covers the OSS internal endpoint, cost and lifecycle guidance.
- Regression: existing fixed pool, public-source fallback and manual `docker pull` paths are unaffected; targeted backend/runner/contract tests, `npm test`, `npm run verify:docs` and `npm run verify:flow` are green.
- Docs: `docs/architecture.md`, `docs/development.md`, `runner/README.md` and the flow issue are updated, and the bilingual pairing and i18n records pass the gates.
