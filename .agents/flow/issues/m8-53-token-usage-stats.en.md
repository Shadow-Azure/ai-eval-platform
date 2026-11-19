# [m8-53] Add token usage statistics to case lists and dataset details

English | [中文](m8-53-token-usage-stats.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-53-token-usage-stats.*
  - prototype/**
  - reports/**
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - web/src/**
  - backend/app/**
  - tests/backend/**
  - docs/**
adr: []
github:
  number: 332
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/332
```

## Background

The production evaluation detail’s case list shows execution, result, trace, test points, and duration, but no tokens; the results tab only has resolve-rate metrics; the dataset pages have no token totals. The trace page already stores real per-attempt tokens (example: 609k tokens / ¥2.56), but they are not aggregated to the case-set or dataset level. With a six-case list, users cannot spot expensive cases directly, and cannot run cost regressions by dataset. Source: Feishu request on 2026-11-19 (with a production case-list screenshot).

## Goals

- Case set (the case list of one evaluation): show tokens and cost per case, and a summary above the table (total tokens, input/output, average per case, total cost, token coverage), with sorting by tokens.
- Dataset level: show total tokens, average per case, average per evaluation, and total cost on the dataset detail and dataset list; show per-case average tokens in the case preview, and support a token trend by version.
- Accounting: token = `input_tokens + output_tokens`; cache read/write tokens are shown separately and excluded from the total; the cost currency must match the actual unit of the trace `cost-state` (currently stored as USD but rendered as CNY, so fix or label it explicitly).
- Prototype first: this issue first updates `prototype/` with before/after screenshots; the production implementation follows with the data path.

## Acceptance

- Prototype: the case list shows the case-set token summary plus token/cost columns; the dataset detail shows dataset-level token cards and a token column in the case preview; the dataset list card shows total tokens; before/after screenshots are attached to this issue and sent via Feishu.
- Production: the results, case list, and dataset pages match the prototype; a run’s page totals match the trace aggregation (allowing ±1 rounding).
- Sorting, filtering, and CSV export include token and cost fields.
- `npm test`, `npm run verify:docs`, and `npm run verify:flow` pass; Web `npm run typecheck` passes.
