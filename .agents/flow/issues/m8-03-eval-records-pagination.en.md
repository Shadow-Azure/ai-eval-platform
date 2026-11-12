# m8-03 Paginate evaluation run records 10 per page

English | [中文](m8-03-eval-records-pagination.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-03-eval-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - web/src/pages/EvalsPage.tsx
  - plans/**
adr: []
github:
  number: 181
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/181
```

## Background

The production evaluation run list currently renders every record at once (`pagination={false}` in `web/src/pages/EvalsPage.tsx`). On 2026-11-12 production has 35 runs; at a 1440-wide viewport the page is about 3168px tall (about 3.5 screens) and there is no pager at the end, so browsing and locating a run requires long scrolling.

## Goals

- Paginate the evaluation run list with a fixed page size of 10 records, starting on page 1.
- Keep the existing columns, filter bar, stat cards and auto-refresh.
- Pagination changes presentation only, not the data source or filter semantics; changing a filter returns to page 1.

## Acceptance

- With more than 10 records only 10 are shown, and the pager reaches all records; total and page numbers are correct, and a final page with fewer than 10 records renders normally.
- With 10 or fewer records the table renders normally without empty pages.
- After a search, status filter or dataset filter change the pager returns to page 1, and the filtered total and page count are correct.
- Existing columns, status tags and the 3-second auto-refresh behavior are unchanged.
- `web` build and typecheck pass; the PR includes before/after screenshots captured in the same scenario.
