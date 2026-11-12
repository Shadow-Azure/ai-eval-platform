# Load real data into overview recent evaluations and paginate 5 per page

English | [中文](m8-02-recent-evals-pagination.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-02-recent-evals-pagination.*
  - .agents/flow/issues/m8-01-runner-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/api/overview.ts
  - web/src/pages/OverviewPage.tsx
  - web/src/pages/EvalsPage.tsx
  - web/src/utils/evaluationDisplay.ts
  - web/src/styles/overview.css
  - plans/**
adr: []
github:
  number: 180
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/180
```

## Background

The overview page's "recent evaluations" card always shows "no evaluation tasks", while the evaluation tasks page has 35 records. Cause: the Table in `web/src/pages/OverviewPage.tsx` is hard-coded with `dataSource={[]}` and never requests the evaluation list; `/api/v1/overview` only returns counts, not recent items; the flat header fields do not match `EvaluationSummary`. The 2026-11-08 PR #19 review already recorded "the recent-evaluations table is a fixed empty array; replace it after M2-05 wires the API", but no follow-up happened. M8-02 wires real data with 5-per-page pagination as a defect fix, without expanding feature scope. This PR also moves #178 (m8-01) from `in-progress` to `done`, because #178 was merged and closed without the corresponding flow update; that one-line closeout restores the flow-online gate and changes no product code.

## Goals

- Read `/api/v1/evaluations` for the overview "recent evaluations" card, ordered by creation time descending, consistent with server-side pagination.
- Show a fixed 5 records per page, starting on page 1, with paging across all records.
- Keep the existing columns and add real field mappings: evaluation task, Agent / model, dataset, status, progress, core metric, start time; add the prototype's detail entry at the end of each row.
- Give distinct loading, failure and truly-empty feedback; remove the stale "under construction M3" marker.

## Acceptance

- With the current 35 production records, page 1 shows 5 records and the pager has 7 pages; page 2 shows records 6–10; the total matches the evaluation tasks page.
- With 5 or fewer records there is no blank page; only 0 records shows "no evaluation tasks"; a failed request can be retried.
- `web` typecheck/build pass; the PR includes before/after screenshots of the same scenario.
- Smoke testing covers overview paging and detail navigation; results and prototype comparison go in the PR description.
