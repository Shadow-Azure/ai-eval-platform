# Implement baseline comparison and configuration diffs

English | [中文](m5-06-baseline-comparison.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: ready
scope:
  - .agents/flow/issues/m5-06-baseline-comparison.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 88
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/88
```

## Background

The results page currently shows only a single-evaluation snapshot, so users cannot determine the effect of changes to model, runtime, assets, dataset version, or execution policy. The target product must recommend comparable baselines, show metric deltas and configuration differences, and avoid combining incomparable evaluations.

## Goals

- Define comparability conditions covering at least dataset version or case set, metric definitions, runtime, model, and resource pool, with an explanation for each baseline recommendation.
- Recommend the most recent comparable final evaluation automatically, allow manual baseline selection, and explicitly identify fields that make an evaluation incomparable.
- Show deltas for primary metrics, supporting metrics, and cost efficiency, with safe fallback for unknown metric renderers.
- Show configuration differences between the current evaluation and baseline, including agent asset commit, model, runtime, concurrency, timeout, retry, and resource pool.

## Acceptance

- When comparable history exists, the UI recommends a baseline with a reason; when none exists, it shows an empty state instead of selecting an arbitrary evaluation.
- Changing the baseline updates metric deltas and configuration differences, while incomparable conditions are visible and different denominators or metric versions are never compared directly.
- Number, percentage, cost, duration, and distribution metrics compute deltas in their own forms, and unknown metrics do not block the page.
- API and frontend tests cover baseline selection, recommendation rules, incomparable cases, empty states, and configuration differences.
