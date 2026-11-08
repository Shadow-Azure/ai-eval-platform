# Build dataset and import product pages

English | [中文](m5-01-dataset-pages.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - web/**
  - backend/**
  - tests/**
  - package.json
  - package-lock.json
  - docs/**
  - plans/**
  - reports/**
adr: []
github:
  number: 33
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/33
```

## Background

Dataset import currently depends on the API or seed data; the primary path must let users configure a source, validate it, confirm the import and browse cases in the web UI.

## Goals

- Build dataset list, detail, version, case and metric pages that reuse the prototype information architecture and design tokens.
- Build a three-step import wizard: source configuration, validation progress and version confirmation, including partial failures.
- Wire server-side pagination, filtering, search and error states, with back and cancel actions.
- Add component tests and visual comparison for key components and the wizard.

## Acceptance

- A user completes an import from the page and sees the case total, metric definitions and case detail.
- Logged-out, unauthorized, invalid-source and partial-failure states are all explicit in the UI.
- Pages match the prototype at 1440×900, and primary actions remain usable at 1280×800.
- Key interactions have component tests and errors are never silent.
