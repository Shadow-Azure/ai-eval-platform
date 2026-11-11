# ADR 0001: Evaluation run status enum and "partial success" semantics

English | [中文](0001-eval-run-status-enum.md)

- Status: accepted
- Date: 2026-11-11
- Applies to: m5-16 (#144, eval list stats and filters), m5-17 (#145, standalone eval detail route)
- Design basis: `plans/m1-02-fullstack-design/06-execution-and-resource-pools.md` §2

## Background

The evaluation list and detail must show one run-status vocabulary, but the origin of that enum was never recorded on its own. The list page previously rendered only "running / failed / completed / cancelled", while the terminal set also included "partial success". If the frontend inferred status from case counts, the same case batch could read differently in the list and the detail.

"Partial success" means: every logical case has settled, at least one case failed, timed out or was cancelled, and usable results remain. It cannot collapse into "failed" (results are still consumable) or into "completed" (not everything succeeded).

## Decision

The backend `EVAL_RUN_STATUSES` is the single source of truth for statuses:

| Status | Meaning | Terminal |
|---|---|---|
| `preparing` | Preparing launch configuration | No |
| `queued` | Waiting for capacity or dispatch | No |
| `running` | Cases are executing | No |
| `finalizing` | Aggregating the final result | No |
| `completed` | Every logical case settled successfully | Yes |
| `partial` | Every logical case settled, at least one failed/timed out/cancelled, usable results remain | Yes |
| `failed` | Terminal failure (no consumable result or dispatch failure) | Yes |
| `pausing_budget` | Budget exhausted, pause is settling | No |
| `paused_budget` | Budget paused, waiting for more budget | No |
| `cancelling` | Cancellation request is being processed | No |
| `cancelled` | Cancelled | Yes |

Rules:

1. The control plane writes the status; the frontend only renders labels for enum members and never derives a new status from success/failure counts.
2. The terminal set is `completed / partial / failed / cancelled`; everything else is active, and the list's "running" stat card and auto refresh use the active set.
3. The boundary between `partial` and `failed` belongs to the execution finalizer; the list query must not recompute it and only reads `eval_run.status`.
4. The list `status` filter accepts only the enum values above; an unknown value returns `422 evaluation_invalid`.
5. The list resolve rate aggregates scored cases (`resolved / (resolved + unresolved)`); unscored cases stay out of the denominator and the frontend only formats the server value.
6. Changing the enum or the terminal set requires updating this ADR, the backend enum and the frontend `web/src/utils/evalStatus.ts` together; no migration is needed because the existing `CheckConstraint` already carries the status constraint.

## Consequences

- The m5-16 list stat cards, status filter and "partial success" rendering follow this ADR.
- The m5-17 detail header stats and status-stacked progress bar reuse the same enum and terminal set instead of defining another status.
- Shared frontend mappings live in `web/src/utils/evalStatus.ts`; list, detail and overview tags derive from it, so pages cannot drift into separate vocabularies.
