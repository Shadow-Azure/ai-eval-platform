# m8-01 Paginate runner records 5 per page

English | [中文](m8-01-runner-records-pagination.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-01-runner-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/pages/OperationsPage.tsx
  - web/screenshots/**
adr: []
github:
  number: 178
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/178
```

## Background

The runner table under Operations → Execution resources currently renders every record at once (`pagination={false}` in `web/src/pages/OperationsPage.tsx`). Production currently has 44 runners, so the table makes the page very long and hard to browse; it should paginate at 5 records per page.

## Goals

- Paginate the runner table with a fixed page size of 5 records, starting on page 1.
- Keep the existing Runner, pool, status, last heartbeat and label columns and status tags.
- Keep the 30-second auto-refresh on the operations page; pagination changes presentation only, not the data source.

## Acceptance

- With more than 5 records only 5 are shown, and the pager reaches all records; total count and page numbers are correct, and a final page with fewer than 5 records renders normally.
- With 5 or fewer records the table renders normally without empty pages.
- Existing columns, status tags and 30-second auto-refresh behavior are unchanged.
- `web` build and typecheck pass; the PR includes before/after screenshots captured in the same scenario.
