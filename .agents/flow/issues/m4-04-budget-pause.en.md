# Pause and resume evaluations on model budget

English | [中文](m4-04-budget-pause.md)

```yaml flow
kind: issue
milestone: m4
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - backend/**
  - runner/**
  - runner-contract/**
  - tests/**
  - deploy/**
  - .github/workflows/**
  - docs/**
  - plans/**
  - reports/**
  - pyproject.toml
  - docker-compose.yml
adr: []
github:
  number: 31
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/31
```

## Background

Long evaluations can generate uncontrolled model cost. Reaching the budget should pause safely instead of failing, and after the user adds budget the unfinished cases continue.

## Goals

- Add budget, spent amount and budget_version to eval_run, with the gateway accumulating cost per attempt and checking the version.
- Implement the pausing_budget to paused_budget flow: stop new dispatches, reject new model calls and wait for active attempts to exit.
- Provide a resume-budget API that atomically adds budget, writes an audit record and requeues unfinished cases.
- Do not generate final results, switch models or switch resources while paused.

## Acceptance

- No new model calls occur after the budget is reached, and completed case results are retained.
- Resume schedules only unfinished cases, creating new attempts for interrupted ones.
- Concurrent confirmation and repeated resume requests are idempotent, and a stale budget_version is rejected.
- Pause, resume and budget overflow are covered by automated tests.
