# Evaluation workbench UI and first real-case end-to-end

English | [中文](m3-04-eval-workbench-e2e.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/issues/m3-04-eval-workbench-e2e.*
  - web/**
  - backend/**
  - deploy/**
  - tests/**
  - reports/**
adr: []
github:
  number: 48
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/48
```

## Background

M2-05 delivered the dispatch/execution/callback/persistence loop and an initial `/evals` page. M3 builds the dataset import wizard, evaluation launch wizard, and result views on top (requirements 2/5/7), and closes the leftover of wiring real Aliyun OSS (the SigV4 path was proven against a MinIO equivalent in M2-05). The final evidence of M3 is one real SWE-bench case executing, scoring, and returning queryable results through the real model gateway and the Claude Code runtime on the fixed Runner.

## Goals

- Dataset import three-step wizard and dataset index/detail pages; an evaluation launch four-step wizard showing ref resolution and consistency checks (blockers visible).
- Progress view: SSE change notifications plus DB snapshot and event-cursor recovery (decision D6); refresh and reconnect neither lose progress nor regress.
- Result view: the two dimensions of execution status versus resolution outcome (decision D5), per-test results, and score/trace object links.
- Wire real Aliyun OSS: inject OSS/S3 credentials into the host `.env`, replacing the MinIO equivalent, and verify the real upload path.
- End-to-end: one real SWE-bench case executes on `fixed-linux-eval` through the model gateway and the Claude Code runtime, is scored with the official metric, and returns queryable results and objects.

## Acceptance

- The UI aligns with the `prototype/` mockups; the difference list is recorded and addressed item by item in the PR.
- The real-case attempt produces score and trace objects and a terminal state persisted in PostgreSQL, queryable via the results API.
- Re-triggering the same evaluation creates an independent attempt; previous results and traces are never overwritten.
- Consistency-check blockers are visible and explainable inside the launch wizard.
- Real OSS uploads succeed with passing HEAD verification; a smoke report (computer-use-smoke-reporting format) is produced and honestly notes the GUI-smoke substitution.
