# [m8-51] repository_dispatch payload transport guard and top-level field reduction

English | [中文](m8-51-dispatch-payload-guard.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-51-dispatch-payload-guard.*
  - backend/app/runner_contracts.py
  - backend/app/services/dispatcher.py
  - backend/app/services/github_dispatch.py
  - docs/architecture.*
  - plans/2026-11-19-pr*-review.md
  - plans/m1-02-fullstack-design/**
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner/lib/json-schema-subset.mjs
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
- Bring `client_payload` back to 10 top-level fields while keeping the workflow able to fetch the scripts bundle pin.
- Distinguish 422 responses: parameter validation is terminal, spam/rate limiting still backs off.
- Add regression tests and documentation, and pin the 10-property cap in the contract schema.

## Plan

- Add a `DispatchPayloadInvalid` preflight in `github_dispatch`: `client_payload` must be a JSON object with at most 10 top-level properties and at most 60KB serialized (GitHub's hard limit is 64KB), and `event_type` must be at most 100 characters; the error carries the actual property count or byte size.
- On a preflight error the dispatcher calls `_exhaust_dispatch` directly, records `dispatch_payload_invalid`, moves the cases to `dispatch_failed`, and never calls the transport.
- A 422 whose body does not contain `spammed` is non-retryable and fails terminally; a spammed 422, 429, 5xx and network errors keep the exponential backoff.
- The `scripts_bundle` pin stays in the outbox row only and is no longer copied into `client_payload`; the workflow reads the pin from the outbox row through the shard `/scripts` metadata endpoint.
- `dispatch.schema.json` gains `maxProperties: 10` and drops the no-longer-sent `scripts_bundle` field; the Python and Node schema subsets gain `maxProperties`/`minProperties`.

## Acceptance

- With a complete bundle configuration, `client_payload` has 10 top-level keys and serializes below 60KB, and the dispatch succeeds; `scripts_bundle` remains in the outbox row and is readable through the `/scripts` endpoint.
- Payloads with 11 properties, more than 60KB, or an `event_type` longer than 100 characters are rejected locally, the transport is not called, and the error names the property count or byte size.
- 422 parameter errors do not retry; a spammed 422, 429 and 5xx still retry, all covered by unit tests.
- The schema rejects 11 top-level properties; the Python and Node validators agree on `maxProperties`.
- `pytest tests/backend`, `npm test`, `npm run verify:flow`, and `npm run verify:docs` pass.

## Scope

- Files: `backend/app/services/dispatcher.py`, `backend/app/services/github_dispatch.py`, `backend/app/runner_contracts.py`, `runner-contract/schemas/v1/dispatch.schema.json`, `runner/lib/json-schema-subset.mjs`, `tests/backend/`, `tests/runner/`, `docs/architecture.*`, `plans/m1-02-fullstack-design/`.
- Out of scope: queue timeout and six-case scheduling (#307/#315), model/dataset/runner execution logic, the bad-case reporting itself, and `workflow_dispatch.inputs`.
