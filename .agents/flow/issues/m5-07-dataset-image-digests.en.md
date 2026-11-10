# Bind execution and scoring image digests to datasets

English | [中文](m5-07-dataset-image-digests.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: ready
scope:
  - .agents/flow/issues/m5-07-dataset-image-digests.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - reports/**
  - plans/**
adr: []
github:
  number: 104
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/104
```

## Background

The launch wizard currently asks for both the execution image digest and the scoring image digest on every run; the platform has defaults, but dataset versions do not record their own image configuration, so selecting a dataset still leaves manual confirmation.

## Goals

- Dataset versions record execution and scoring image digests supplied at import; when omitted, the platform default image configuration applies.
- Dataset detail shows the digests bound to the selected version.
- After a dataset version is selected, the launch wizard pre-fills the digests and still allows a per-run override; the server resolves missing values from the dataset version first, then the platform defaults.

## Acceptance

- Imports with and without digests both produce reproducible version records, and omitted values match the platform configuration.
- With a selected version, preflight passes without manual entry; explicit overrides and the fully missing path each have test coverage.
- Dataset detail exposes the bound values, and backend preflight, creation and related documentation are updated together.
