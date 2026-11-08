# Add official SWE-bench scoring, per-test results and metric aggregation

English | [中文](m3-04-swebench-scoring.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - runner/**
  - runner-contract/**
adr: []
github:
  number: 26
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/26
```

## Background

Real evaluations must use the official SWE-bench scoring semantics and retain each test status, group and failure log, not just a resolved boolean. The platform currently has no scoring artifacts, test-level results or metric aggregation tables.

## Goals

- Integrate the official scoring image or harness, parse report.json and test output, and retain per-test F2P and P2P results.
- Add score_artifact, test_case_result and metric_result tables with an import service.
- Implement metric registration and whitelisted aggregators for resolved_count, resolved_rate, resolved_rate_scored, execution_failure_rate, cost and duration metrics.
- Expose case test detail and evaluation result APIs that distinguish running snapshots from final results.

## Acceptance

- Positive and negative scoring reports parse into resolved, unresolved and not_scored plus test-level outcomes.
- The resolved_rate denominator is the selected case total; errors, empty patches and unfinished cases are not removed from it.
- Final results do not change with metric configuration, and recomputation creates a new version with an audit trail.
- Result APIs return the metric version, formula, denominator, is_final and generation time.
