# [m10] Template machine launch: candidate-commit injection and service tokens

English | [中文](m10-candidate-launch-override.md)

```yaml flow
kind: issue
milestone: m10
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m10-candidate-launch-override.md
  - .agents/flow/issues/m10-candidate-launch-override.en.md
  - .agents/flow/issues/m10-candidate-launch-override.i18n.yaml
  - plans/2026-11-25-m10-candidate-launch-override/**
  - backend/app/api/eval_templates.py
  - backend/app/services/eval_templates.py
  - backend/app/api/results.py
  - backend/app/api/dependencies.py
  - backend/app/models/evaluation.py
  - backend/app/models/identity.py
  - backend/app/models/__init__.py
  - backend/app/services/service_tokens.py
  - backend/app/api/service_tokens.py
  - backend/app/main.py
  - backend/migrations/**
  - tests/backend/test_eval_templates.py
  - tests/backend/test_results.py
  - tests/backend/test_service_tokens.py
  - tests/backend/test_operations.py
  - tests/backend/test_deployment_migration.py
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 429
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/429
```

## Background

The three stable execution APIs from M10 speak console-session semantics: launch accepts only an administrator Cookie session and its body carries just an idempotency key plus an optional name, and status/results sit behind a session too; harness-evolution's self-evolution loop needs to trigger evaluations as a machine — after each round publishes a candidate commit, it pins a template evaluation to that commit, polls the status, and fetches the measurements. The requester confirmed the scope in a discussion on 2026-11-25, which converged into this issue; the full analysis and discussion record is in "Deliverables" below.

## Analysis process

- The override pipeline already exists: the run-level `POST /api/v1/evaluations` has long supported `asset_source_id / requested_ref / asset_paths / asset_types` and freezes an immutable asset snapshot (`backend/app/api/evaluations.py`, `backend/app/services/agent_assets.py`); what is missing is only the parameter entry on template launches.
- Asset snapshots are frozen by commit (`resolved_commit`, tree digest, bundle), so moving a branch after launch cannot change a running evaluation; `resolve_commit` accepts branch/tag/SHA and a SHA resolves to itself, which directly supports "commit as the authority plus an assertion".
- The template launch chain (`launch_template`) currently resolves assets only from the template's own source and ref; adding override parameters reuses the existing `_resolve_template_assets → build_preparation → consume_preparation` path, so no new execution path is needed.
- The platform has no machine identity: launch requires an administrator session and status/results sit behind a session; harness-evolution cannot hold a browser session, so a service token is the only identity layer that must be added.
- The platform run state machine has 11 states (including partial/paused/cancelled) while the HE poller knows only 5; per D1 the adaptation happens in HE and the platform contract does not change for it.
- Platform results carry only measurements (metric items plus `is_final`) with no verdict; per D4 the verdict stays in HE, and the candidate-evaluation template keeps self-evolution reporting disabled (D5), so duplicate reporting is a non-issue.

## Discussion conclusions with the requester

The requester confirmed the following five decisions one by one on 2026-11-25; the V1 gap analysis converged into the V2 design based on them, and this issue is its landing vehicle.

| Decision | Conclusion | Impact on the design |
|---|---|---|
| D1 contract direction | HE adapts to the platform; the platform contract does not bend toward a generic `/evals` shape | New logic follows the platform's existing style; HE writes the adapter |
| D2 injection parameters | The trigger API supports optional parameters: repository + commit + asset configuration | launch gains a controlled override block; the commit is the authoritative identity with a resolution assertion |
| D3 cross-run activation | The platform does not own promotion; after evaluation HE opens a PR and a human decides the merge | No promotion/merge design in the platform; the template ref target is an operations convention |
| D4 measurement and verdict | The platform returns measurements only; the verdict happens in HE | No verdict/policy fields on the platform; the results API keeps its measurement semantics |
| D5 bad case reporting | The self-evolution evaluation template keeps reporting disabled | Duplicate reporting/dedup is a non-issue; an override launch with a reporting-enabled template only warns |

## Requirements

1. HE adapts to the platform mechanism; the platform contract evolves in its own style and does not become a generic `/evals` shape for HE.
2. The trigger API must support optional parameters: the asset configuration of a chosen repository and commit id can be injected into one evaluation; without the parameters the one-click template semantics stay unchanged.
3. The commit is the authoritative identity: injection must resolve by commit, freeze the snapshot, and assert the resolved result; ref is display/audit only.
4. The platform returns measurements only and does not make accept/reject verdicts; the verdict policy is implemented by HE.
5. The evaluation template used for candidate evaluations keeps self-evolution bad case reporting disabled; the platform does not design dedup for it.
6. Cross-run activation is out of the platform's scope: after evaluation HE opens a PR and a human decides whether to merge.

## Solution overview

The platform-side changes concentrate in three files plus one new table: optional fields on the request model, override resolution plus assertion in launch, asset identity echo on status/results, and the service-token table; asset resolution, snapshot freezing, and the prepare/create chain are all reused, with no new execution path.

Interaction flow: HE publishes a candidate → calls launch with `asset_override` → the platform validates, freezes the snapshot, and echoes `resolved_commit` → HE compares and then polls status → on the terminal state it fetches results and converts them into verdict inputs (objective/coverage/guardrails) → HE decides and advances the iteration; promotion is a PR from HE with a human merge (D3, outside the platform).

- `LaunchTemplateRequest` gains optional `asset_override` (repo_url/ref/commit/asset_paths/asset_types) and `lineage`; each field is replaced as a whole and an absent field inherits the template.
- `launch_template` supports override resolution: a missing repo_url uses the template asset source, otherwise the registered source is found by exact repo_url lookup; a commit resolves by itself with an assertion that `resolved_commit` matches; the run stores `asset_override_json / evolution_lineage_json`.
- status/results echo an `asset` block (resolved_commit, asset_snapshot_id, requested_ref); results also echo lineage when provided.
- A new `service_token` table plus `POST/GET/DELETE /api/v1/service-tokens`; launch accepts "administrator session or service token", status/results accept "any session or an `eval:read` token", with template allowlists and concurrency/budget caps.

HE-side adaptation (gateway adapter, state mapping, result mapping, baseline and verdict) progresses in the harness-evolution repository and is not part of this issue.

## Interface changes

### Before

```text
POST /api/v1/eval-templates/{template_id}/launch
  auth:    console session (admin role only); no machine identity
  headers: Idempotency-Key (optional)
  body:    { "idempotency_key": string | null, "name": string | null }
  202 ->   { "run_id", "template_id", "status", "status_url", "result_url", "created" }

GET /api/v1/eval-runs/{run_id}/status
  -> { "run_id", "template_id", "template_version", "status", "terminal", "total_cases",
       "queued_count", "running_count", "succeeded_count", "failed_count", "finished_count",
       "progress": { "total", "finished", "percent" }, "resolved_count", "scored_count",
       "resolve_rate", "started_at", "finished_at", "duration_seconds",
       "status_url", "result_url" }

GET /api/v1/eval-runs/{run_id}/results
  -> { "run_id", "template_id", "status", "is_final", "status_url", "result_url",
       "items": [ { "metric_key", "value", "is_final", ... } ], "token_summary" }
```

### After

```text
POST /api/v1/eval-templates/{template_id}/launch
  auth:    admin session OR service token (Bearer hev_..., scope eval:launch,
           optional template allowlist and budget caps)
  headers: Idempotency-Key (unchanged; replay returns the same run)
  body:    { "idempotency_key": string | null, "name": string | null,
             "asset_override": {                    # all optional; absent = inherit template
               "repo_url": string,                  # default: template asset source
               "ref": string,                       # display/audit only
               "commit": string,                    # 40-hex; authoritative identity
               "asset_paths": [string],             # whole-field replacement
               "asset_types": { string: string } }, # whole-field replacement
             "lineage": {                           # optional; stored on the run only
               "evolutionRunId": string, "evolutionRoundId": string,
               "roundSeq": int, "parentCommit": string } }
  202 ->   previous fields + "asset": { "repo_url", "requested_ref", "requested_commit",
             "resolved_commit", "asset_snapshot_id", "bundle_digest" }
             + "warnings": [ ... ]                  # only when override is present and
                                                    # the template enables self-evolution
                                                    # reporting
  422 ->   candidate_commit_mismatch | asset_source_unresolved | asset_ref_unresolved |
           asset_selection_empty | asset_path_not_found (no run is created)

GET /api/v1/eval-runs/{run_id}/status
  -> previous fields + "asset": { "resolved_commit", "asset_snapshot_id", "requested_ref" }

GET /api/v1/eval-runs/{run_id}/results
  -> previous fields + "asset" (same shape) + "lineage" (echoed when provided)
```

### Input and return changes

| API | Input change | Return change |
|---|---|---|
| launch | Adds optional `asset_override` and `lineage`; authentication extends from "administrator session only" to "administrator session or service token" | Adds the `asset` block and possible `warnings`; other fields and idempotency semantics stay unchanged |
| status | No input change (machines read with an `eval:read` token) | Adds the `asset` block |
| results | No input change | Adds the `asset` block and `lineage` (echoed when provided) |
| service-tokens (new) | `POST /api/v1/service-tokens` creates (the plaintext is returned once, only in the creation response), `GET` lists (masked), `DELETE` revokes | Only the token hash is stored; no response, log, or snapshot ever echoes the plaintext |

### New error codes

| Error code (HTTP) | Trigger condition |
|---|---|
| `candidate_commit_mismatch` (422) | The resolved commit differs from the requested commit; never evaluate silently |
| `asset_source_unresolved` (422) | `asset_override.repo_url` cannot be resolved to a registered asset source by exact repo_url lookup (credentials undeterminable) |
| `asset_ref_unresolved` (422) | commit/ref cannot be resolved in the asset source repository (unreachable repository, invalid credentials, or a missing object) |
| `asset_selection_empty` (422, existing code reused) | The asset selection after override or inheritance is empty |
| `asset_path_not_found` (422, existing code reused) | An asset path does not exist at that commit |

## Deliverables

- [OpenAPI change draft](../../../plans/2026-11-25-m10-candidate-launch-override/openapi-change.md): schema-level diff (request models, response extensions, error codes, service-token endpoints).
- [Docs §5.14 revision text](../../../plans/2026-11-25-m10-candidate-launch-override/docs-5-14-revision.md): replacement paragraphs in Chinese and English, to be applied through the platform documentation flow during implementation.

## Acceptance

- Launch one real candidate commit with an override: the 202 response's `asset.resolved_commit` equals the requested commit verbatim, and status/results echo it consistently.
- Golden injection proof: a candidate modifies one SKILL.md, and the snapshot manifest's digest for that file equals the digest of the corresponding blob in that commit.
- Commit authority: when both ref and commit are given, resolution follows the commit and ref is echoed verbatim; an unresolvable commit alone answers 422 `asset_ref_unresolved` and creates no run.
- Cross-repository injection: an unregistered `repo_url` answers 422 `asset_source_unresolved`; after registration the same request succeeds.
- Empty selection or a missing path after override: 422 `asset_selection_empty / asset_path_not_found`, and no run is created.
- Console one-click launches without `asset_override` behave unchanged: no `asset` block in the response, and permission, idempotency, and validation semantics stay the same.
- Service token: no valid credential (no token, invalid, revoked, or expired) → 401 `unauthenticated`; an insufficient scope or a template outside the allowlist → 403 `forbidden`; reaching the concurrency or daily-budget cap → 429 `service_token_limit_exceeded`; audit records `created_by=svc:<name>`; an idempotent replay returns the same run; `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs`, and PR CI pass.

## Non-goals

- No change to evaluation execution, scheduling, scoring, or the trace chain; the override only affects asset source resolution.
- No accept/reject verdict or policy fields (D4); the verdict is implemented by HE.
- No promotion, merge, or rollback (D3); pointing the template ref at a merged branch or a release tag is an operations convention.
- No bad case dedup or reporting-mode design (D5); reporting semantics are decided entirely by the template configuration.
- HE-side adaptation (gateway, mappings, verdict) is not implemented in this repository.

## References

- Design: [docs/design/eval-platform-architecture.en.md](../../../docs/design/eval-platform-architecture.en.md) §5.14 (evaluation templates and one-click launch).
- Related: issue #378 (evaluation templates), #382 (template-level self-evolution reporting), #410 (template provenance display).
- Platform state machine and results read model: `backend/app/models/evaluation.py`, `backend/app/api/results.py`.
- Evidence of the existing override pipeline: `backend/app/api/evaluations.py` (CreateEvaluationRequest), `backend/app/services/agent_assets.py` (resolve_assets).
