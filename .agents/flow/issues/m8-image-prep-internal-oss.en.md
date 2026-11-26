# Internal OSS image preparation: consumption monitoring, queued/claimed split and TTL fix

English | [中文](m8-image-prep-internal-oss.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/**
  - tests/**
  - docs/**
  - deploy/**
  - scripts/**
  - .agents/flow/**
adr: []
github:
  number: 436
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/436
```

## Background

- A template launched through the machine API stays at preparing while image-prep does not consume the queue, so the caller only receives repeated 409 image_not_ready.
- Automatic preparation uploads through the public OSS endpoint and is bounded by the deployment host's public egress; the serial queue and the fixed one-hour preparation TTL are shorter than the real preparation time.
- preparing means both "queued, never claimed" and "processing", so a 30-minute stale window misreads a legitimate queue; a restart also deletes an exported temporary tarball.

## Goal

- Upload and HEAD through the internal OSS endpoint, falling back to the public endpoint for non-Aliyun, cross-region or unreachable internal deployments.
- Write worker heartbeat and queue depth to service_heartbeat; expose worker plus queued/processing in readiness and return a clear launch error while the worker is offline.
- Record claimed_at / lease_owner; make GET read-only and reap only never-claimed overdue rows from ensure while the worker is offline.
- Derive the preparation TTL from the image count and renew it while the queue progresses.
- Let service tokens read readiness and drive ensure/retry; return readiness_url and retry_after_seconds in 409/503.
- Keep a completed temporary tarball so an upload failure or restart re-uploads it directly; start/update image-prep on deploy when no preparation is active.

## Acceptance

- Tests cover internal endpoint selection and public fallback; upload and HEAD use the same endpoint.
- Tests cover heartbeat, queued/processing, the offline 503, read-only GET and offline reaping.
- Tests cover the claimed_at / lease_owner migration and that a reaped row stays failed.
- Tests cover TTL derivation by image count and ensure/retry renewal.
- Tests cover service-token eval:read / eval:launch artifacts access and readiness_url.
- Tests cover complete-tarball reuse after upload failure/restart and the sweep retention policy.
- Migration, deployment contract, documentation gates and the full backend suite pass.
