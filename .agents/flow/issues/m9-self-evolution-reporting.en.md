# Self-evolution reporting: launch config, bad case reporting, and session trajectory download

English | [中文](m9-self-evolution-reporting.md)

```yaml flow
kind: issue
milestone: m9
priority: P0
status: in-progress
scope:
  - .github/**
  - backend/app/**
  - backend/migrations/**
  - deploy/**
  - scripts/**
  - web/src/**
  - prototype/**
  - tests/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 263
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/263
```

## Background

harness-evolution (the self-evolution platform) has merged the per-report session source mechanism (Shadow-Azure/harness-evolution#286, issue #282): the bad case ingest contract `POST /api/workspaces/{wid}/bad-cases` gains an optional `sessionSource` field (`runtime` + `sessionUrlTemplate`), and trajectory backlink now calls a single-envelope endpoint resolved from the URL template carried by the report (returning runtime identity, one level of childSessionIds, and normalized NDJSON trajectory in one call); authorization relies on the short-lived signature embedded in the template and no credential fields are accepted.

The evaluation platform lacks the sending side: evaluations cannot configure a self-evolution reporting target at launch, non-resolved cases are not reported after execution, and the platform exposes no session download endpoint for harness-evolution to backlink. See `plans/2026-11-16-swebench-badcase-report-proposal.html` for the proposal discussion.

## Goals

- Add an optional "self-evolution reporting" section to the evaluation launch wizard: platform base URL, workspace ID, and ingest credential (whk_ prefix); the credential is masked like other credentials and never echoed in plaintext.
- After a run reaches a terminal state, report every non-`resolved` case from the final SWE-bench outcome (`unresolved` / `execution_failed`, excluding `not_scored`) as a bad case to the target workspace; fields follow the harness-evolution `BadCaseIngest` contract, with a deterministic `badCaseId` so replays are idempotent (re-reporting answers 200 and replaces without consuming the workspace budget).
- Each report carries per-report `sessionSource`: `runtime` is the evaluation agent runtime key and `sessionUrlTemplate` points at this platform's session envelope download endpoint with an embedded short-lived signature.
- Add a session trajectory download endpoint: given a sessionId it returns the envelope JSON (sessionId echo, runtime, one level of childSessionIds, NDJSON trajectory); invalid signatures fail with a stable 401; per-session 64 MiB / 1 MiB per line / 1,000,000 line limits align with the harness-evolution pull side.
- Reporting failures are observable and retried with backoff ladders without blocking the evaluation main flow; 409 budget exhaustion is a terminal failure with an alert.
- Update the prototype and the web wizard together (an optional Step 3 section plus a Step 4 summary row); before/after screenshots are attached to this issue.

## Acceptance

- Backend: after a configured run reaches a terminal state, non-resolved cases get deterministic `badCaseId` values and POST successfully to the target workspace (including `sessionSource`); repeated triggers do not create duplicate bad cases.
- Backend: the session download endpoint returns the envelope for a valid signature (including main/child sessions and the NDJSON trajectory), 401 for invalid or expired signatures, and 404 for a missing session.
- Frontend: the launch wizard allows configuring or leaving empty self-evolution reporting; the Step 4 review page and the summary sidebar show the reporting target; the credential input is masked and never echoed in plaintext.
- Tests: report mapping, idempotency, backoff, signature validation, and envelope assembly (including one-level child expansion and limit defenses) are unit tested; CI is green.
- Docs: relevant `plans/m1-02-fullstack-design/` sections and current-state `docs/` pages are updated; before/after prototype screenshots are attached to this issue.
