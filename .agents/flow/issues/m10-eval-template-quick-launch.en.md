# [m10] Evaluation templates: fixed defaults, one-click launch, and three execution APIs

English | [中文](m10-eval-template-quick-launch.md)

```yaml flow
kind: issue
milestone: m10
priority: P1
status: ready
scope:
  - backend/app/**
  - backend/migrations/**
  - tests/**
  - web/src/**
  - prototype/**
  - docs/**
  - plans/**
  - .agents/flow/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 378
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/378
```

## Background

Launching an evaluation currently takes a four-step wizard that reselects the dataset version, agent runtime and Git assets, model credential, and resource pool every time; these combinations repeat heavily in real use but can only be reproduced from memory. eval_run freezes the configuration of one execution, but there is no reusable task definition and no stable API for trigger, status, and results, so an external script cannot launch a fixed combination by ID.

## Goals

- Add a project-level eval_template that fixes the dataset version, agent runtime, agent asset source/ref/asset selection, model credential, resource pool, case scope, execution policy, prompt suffix, max turns, and agent timeout; templates can be enabled, disabled, and edited.
- Keep only the three most recently used templates as quick-launch entries on the evaluations page, each retaining one-click launch and details; full template management moves to a dedicated evaluation templates list page.
- The evaluation templates list supports search by name, dataset, agent, and model, filtering by enabled state, and pagination ordered by most recent launch; the list API supports q, status, dataset_version_id, limit, offset, and total and is not one of the three execution APIs.
- Support saving as an evaluation template in step 4 of the launch wizard; a saved template enters the template list. Also provide a new-template entry that saves a template without launching.
- Three stable APIs:
  - POST /api/v1/eval-templates/{template_id}/launch: trigger an evaluation from a template and return run_id, status, and status/result URLs; supports an idempotency key.
  - GET /api/v1/eval-runs/{run_id}/status: poll execution status, progress, and whether the run is terminal.
  - GET /api/v1/eval-runs/{run_id}/results: read metric snapshots and is_final; a running run returns partial results explicitly marked non-final.
- Every launch re-resolves the template's asset ref, credential, and resource pool into a fresh immutable snapshot; eval_run records template_id, template version, and the launch snapshot, so later template edits do not affect launched evaluations.
- A template launch cannot override fixed fields; when a referenced dataset, model, resource pool, or asset source is deleted or disabled, the launch is rejected with an explicit error code.
- Update the architecture design document together with the prototype, and review the before/after prototype screenshots in this issue.

## Non-goals

- No scheduled or periodic triggers, no matrix launch with different configurations in one batch, and no template placeholders.
- No separate machine-token system; the first release reuses console session authentication for the three APIs, and launching still requires administrator permission.
- No change to execution, scheduling, scoring, or trace pipelines; templates only reuse the existing prepare/create flow.
- This issue first delivers the design, prototype, and screenshot confirmation; production implementation follows the acceptance items later.

## Acceptance

- The template list shows the fixed combination, latest launch, and status and supports search, status filtering, and server-side pagination; one-click confirmation creates an evaluation and opens its detail, which names the source template.
- The evaluations page always shows only the three most recently used templates and never grows with the total; an empty list result can clear the search and filters.
- A template can be saved from step 4 of the launch wizard or created from the new-template entry without launching; an empty name, an unresolved asset ref, or an invalid reference blocks saving or launching.
- The three APIs return stable fields at the paths above; status returns terminal=true at a terminal state, results returns is_final=false while running and complete metrics after the terminal state.
- Replaying an idempotency key returns the same run_id; a disabled template returns 409; a missing template or evaluation returns 404; an unauthorized launch returns 403.
- npm test, npm run verify:flow, npm run lint:docs, npm run verify:docs, the relevant backend tests, and the frontend build pass; prototype screenshots use the same viewport and theme and are attached to this issue.

## References

- Design document: the "评测模板与一键启动" section of `docs/design/eval-platform-architecture.en.md`.
- Prototype: the evaluations page, step 4 of the launch wizard, and the template detail page in `prototype/index.html`.
