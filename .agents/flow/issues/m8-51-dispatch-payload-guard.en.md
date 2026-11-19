# [m8-51] repository_dispatch payload transport guard and top-level field reduction

English | [中文](m8-51-dispatch-payload-guard.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-51-dispatch-payload-guard.*
  - .github/workflows/eval-shard.yml
  - backend/app/api/runners.py
  - backend/app/runner_contracts.py
  - backend/app/services/case_attempts.py
  - backend/app/services/dispatcher.py
  - backend/app/services/evaluations.py
  - backend/app/services/github_dispatch.py
  - backend/app/services/spot_fallback.py
  - docs/architecture.*
  - plans/2026-11-19-pr*-review.md
  - plans/m1-02-fullstack-design/**
  - runner/**
  - runner-contract/**
  - tests/**
adr: []
github:
  number: 323
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/323
```

## Background

A production evaluation was rejected by GitHub during dispatch with a 422: `client_payload` allows at most 10 top-level properties, and #312 copied `scripts_bundle` into the payload for image-cache acceleration, which made it 11. No workflow run was created; the worker treated the 422 as retryable, exhausted the backoff budget and reached `dispatch_failed`, so no case ever started and bad-case reporting had no fallback.

## Goals

- Validate GitHub's payload limits locally before the POST; fail fast and diagnosably without calling the transport or consuming the retry budget.
- Adopt option C: keep only the eight pre-start top-level fields in `client_payload`, serve the digest/expiry/scripts-bundle pin from a server descriptor, and keep the workflow able to fetch the scripts bundle pin.
- Distinguish 422 responses: parameter validation is terminal, spam/rate limiting still backs off.
- Add regression tests and documentation, and version the contract schema while pinning the 10-property cap.

## Plan

- Add a `DispatchPayloadInvalid` preflight in `github_dispatch`: `client_payload` must be a JSON object with at most 10 top-level properties and at most 60KB serialized (GitHub's hard limit is 64KB), and `event_type` must be at most 100 characters; the error carries the actual property count or byte size.
- On a preflight error the dispatcher calls `_exhaust_dispatch` directly, records `dispatch_payload_invalid`, moves the cases to `dispatch_failed`, and never calls the transport.
- A 422 whose body does not contain `spammed` is non-retryable and fails terminally; a spammed 422, 429, 5xx and network errors keep the exponential backoff.
- New and retry dispatches use the schema 1.1 eight fields: `schema_version`, `eval_run_id`, `shard_id`, `attempt`, `dispatch_key`, `manifest_url`, `runs_on`, `job_timeout_minutes`; digest and expiry stay in the outbox row and are served by the manifest response's `X-Manifest-Digest` / `X-Manifest-Expires-At` headers.
- The `scripts_bundle` pin stays in the outbox row only and is read back through the shard `/scripts` metadata endpoint; the schema 1.0 legacy shape stays accepted so in-flight dispatches survive a rolling deploy.
- `dispatch.schema.json` distinguishes 1.0/1.1 through `schema_version` in a `oneOf` and keeps `maxProperties: 10`; the Python and Node validators share the same branch semantics.

## Acceptance

- With a complete bundle configuration, the schema 1.1 `client_payload` has 8 top-level keys and serializes below 60KB, and the dispatch succeeds; `scripts_bundle`, `manifest_digest` and `expires_at` stay in the outbox row and are readable through the manifest response headers and the `/scripts` endpoint.
- Payloads with 11 properties, more than 60KB, or an `event_type` longer than 100 characters are rejected locally, the transport is not called, and the error names the property count or byte size.
- Both a legacy 1.0 dispatch and a minimal 1.1 dispatch pass the contract; a 1.1 dispatch missing `runs_on`/`job_timeout_minutes` is rejected.
- 422 parameter errors do not retry; a spammed 422, 429 and 5xx still retry, all covered by unit tests.
- The schema rejects 11 top-level properties; the Python and Node validators agree on `oneOf` and `maxProperties`.
- `pytest tests/backend`, `npm test`, `npm run verify:flow`, and `npm run verify:docs` pass.

## Scope

- Files: `backend/app/api/runners.py`, `backend/app/services/{dispatcher,evaluations,case_attempts,spot_fallback,github_dispatch}.py`, `backend/app/runner_contracts.py`, `runner-contract/`, `runner/lib/`, `runner/scripts/`, `.github/workflows/eval-shard.yml`, `tests/backend/`, `tests/runner/`, `tests/integration/`, `docs/architecture.*`, `plans/m1-02-fullstack-design/`.
- Out of scope: queue timeout and six-case scheduling (#307/#315), model/dataset/runner execution logic, the bad-case reporting itself, and `workflow_dispatch.inputs`.
