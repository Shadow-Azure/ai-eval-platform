# [m8-53] Include cache input and its composition split in token stats

English | [中文](m8-53-cache-token-accounting.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-53-cache-token-accounting.*
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - plans/m1-02-fullstack-design/08-trace-normalization.md
  - plans/2026-11-19-pr*-review.md
  - prototype/**
  - reports/**
  - backend/app/**
  - tests/backend/**
  - web/src/**
adr: []
github:
  number: 339
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/339
```

## Background

After PR #336 shipped, production token stats only showed non-cache input + output and excluded Claude Code cache tokens. Production run `0a51941d` (six cases) reported 1,176,376 non-cache input tokens and 425,226 output tokens, while `gen_ai.usage.cache_read_input_tokens` was 47,890,419; remote model usage usually counts cache as input, so the platform numbers looked visibly too small. Separately, dataset cumulative tokens covered only 49 of 99 evaluations with traces, and the cards did not show that coverage.

## Goals

- Count cache input in the user-facing total while keeping four splits: total tokens (with cache), cache input (read / creation), non-cache input, and output. The non-cache fields from #332 keep their meaning; new fields are backward compatible.
- Aggregate from LLM span `gen_ai.usage.cache_read_input_tokens` / `cache_creation_input_tokens`, still excluding the AGENT summary span; one SQLAlchemy JSON expression serves both SQLite tests and PostgreSQL production.
- `/cases`, `/evaluations/{id}`, `/results`, datasets and trace endpoints return the cache fields; the Web UI uses the same accounting on case lists, results, dataset detail/list and the trace viewer.
- Dataset cumulative cards explicitly show “N/M evaluations with traces”; evaluations without a parsed trace do not dilute averages and are not treated as zero.
- Build the UI from the issue #339 `prototype`; compare against the prototype and the design document before delivery and fix differences.

## Acceptance

- Total (with cache) = non-cache input + cache input (read + creation) + output, and the numbers reconcile; cache is never folded into non-cache input.
- Case rows, summary bar, results, datasets and trace share one accounting; dataset cumulative shows trace coverage.
- Full backend tests (SQLite locally, PostgreSQL in CI), frontend typecheck/build and repository gates pass.
- The PR description contains before/after production UI screenshots and smoke evidence, compared with the prototype.
