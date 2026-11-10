# Make evaluation assets optional and keep Agent required

English | [中文](m5-09-optional-evaluation-assets.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m5-09-optional-evaluation-assets.*
  - web/**
  - tests/backend/**
  - docs/**
  - reports/**
adr: []
github:
  number: 122
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/122
```

## Background

The launch wizard currently treats the Agent asset step as mandatory: without a frozen asset snapshot the operator cannot reach the execution policy step, and `Prepare and check` stays disabled. Users who only need Runtime built-ins are still forced to configure a Git asset source, so they cannot launch the evaluation.

## Goals

- An evaluation can launch without selecting an Agent asset source, using only Runtime built-ins.
- Agent Runtime remains a required input; the operator cannot continue without one.
- With assets optional, the wizard summary, confirmation step and detail page accurately show "no assets selected".

## Acceptance

- With no asset source or Ref selected, the operator can leave step 2, finish preparation and create the evaluation; preparation reports an `assets_empty` warning and the created evaluation has no asset snapshot.
- After selecting an asset source and reading it, the original flow still freezes the snapshot and stores it on the evaluation.
- Without an Agent Runtime, step 2 and preparation show a clear message and cannot continue.
- Backend tests cover preparation and creation without an asset snapshot; the web build passes.
