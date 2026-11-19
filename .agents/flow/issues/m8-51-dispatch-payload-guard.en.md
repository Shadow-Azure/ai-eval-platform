# [m8-51] repository_dispatch payload transport guard and top-level field reduction

English | [中文](m8-51-dispatch-payload-guard.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
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
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner/README.md
  - runner/lib/json-schema-subset.mjs
  - runner/scripts/hydrate-dispatch.mjs
  - scripts/doc-budgets.manifest.json
  - tests/**
adr: []
github:
  number: 323
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/323
```

## Background

A production evaluation was rejected by GitHub during dispatch with a 422: `client_payload` allows at most 10 top-level properties, and #312 copied `scripts_bundle` into the payload for image-cache acceleration, which made it 11. No workflow run was created; the worker treated the 422 as retryable, exhausted the backoff budget and reached `dispatch_failed`, so no case ever started and bad-case reporting had no fallback.

## Goals

- Validate GitHub's payload limits locally before the POST; fail fast and diagnosably without calling the transport or entering the retry backoff.
- A 1.1 dispatch keeps only the 8 start-required fields; `manifest_digest`, `expires_at` and `scripts_bundle` come from a server descriptor after startup and are handed to the runner by the workflow.
- Distinguish 422 responses: parameter validation is terminal, spam/rate limiting still backs off.
- Add regression tests and documentation, and keep the contract schema compatible with both 1.0 and 1.1.

## Plan

- Add a `DispatchPayloadInvalid` preflight in `github_dispatch`: `client_payload` must be a JSON object with at most 10 top-level properties and at most 60KB serialized (GitHub's hard limit is 64KB), and `event_type` must be at most 100 characters; the error carries the actual property count or byte size.
- On a preflight error the dispatcher calls `_exhaust_dispatch` directly, records `dispatch_payload_invalid`, moves the cases to `dispatch_failed`, and never calls the transport; a legacy 11-property row first drops the payload's `scripts_bundle` copy and keeps the outbox pin.
- Only known 422 parameter-validation messages are terminal; a spammed 422, an unknown 422, 429, 5xx and network errors keep the exponential backoff.
- Plan C: a 1.1 dispatch carries only `schema_version`, `eval_run_id`, `shard_id`, `attempt`, `dispatch_key`, `runs_on`, `job_timeout_minutes`, `manifest_url`; a new shard descriptor endpoint verifies the manifest-issued short token, is shard-bound and rate-limited, and returns the digest, expiry and scripts bundle pin.
- After startup the prepare job hydrates the descriptor into the runner-facing dispatch file and passes it to execute; the scripts bundle pin still comes from `/scripts`. A 1.0 dispatch passes through untouched.
- `dispatch.schema.json` uses `oneOf` to accept 1.0 (10 keys) and 1.1 (8 start fields); the Python and Node schema subsets gain `maxProperties`/`minProperties`.

## Acceptance

- With a complete bundle configuration, a 1.1 `client_payload` has 8 top-level keys and serializes at most 60KB, and the dispatch succeeds; `scripts_bundle`, `manifest_digest` and `expires_at` remain on the outbox row.
- The descriptor returns the digest/expiry/bundle pin with a valid short token; a missing or bad token returns 401, a cross-shard token is rejected, and the rate limit returns 429.
- After hydration the workflow verifies the manifest bytes against the descriptor digest and merges the expiry; a legacy 1.0 dispatch skips the descriptor and passes through.
- Payloads with 11 properties, more than 60KB, or an `event_type` longer than 100 characters are rejected locally and the transport is not called; a legacy 11-property row drops the payload copy and still dispatches.
- Known 422 parameter errors do not retry; a spammed 422, an unknown 422, 429 and 5xx still retry, all covered by unit tests.
- Without a payload pin, the workflow still verifies and uses the pre-seeded image cache from the `/scripts` metadata; only a cache miss downloads the bundle.
- The schema accepts 1.0 with 10 keys and 1.1 with 8 keys, and rejects 11; the Python and Node validators agree.
- `pytest tests/backend`, `npm test`, `npm run verify:flow`, and `npm run verify:docs` pass.

## Scope

- Files: `.github/workflows/eval-shard.yml`, `backend/app/api/runners.py`, `backend/app/services/case_attempts.py`, `backend/app/services/dispatcher.py`, `backend/app/services/evaluations.py`, `backend/app/services/github_dispatch.py`, `backend/app/runner_contracts.py`, `runner-contract/schemas/v1/dispatch.schema.json`, `runner/README.md`, `runner/lib/json-schema-subset.mjs`, `runner/scripts/hydrate-dispatch.mjs`, `tests/backend/`, `tests/runner/`, `docs/architecture.*`, `plans/m1-02-fullstack-design/`.
- Out of scope: queue timeout and six-case scheduling (#307/#315), model/dataset/runner execution logic, the bad-case reporting itself, and `workflow_dispatch.inputs`.
