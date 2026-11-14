# [m8-29] Source CLI linker writes executable wrappers

English | [中文](m8-29-source-bin-wrapper.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-29-source-bin-wrapper.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/link-source-bins.mjs
  - tests/runner/agent-source-path.test.mjs
adr: []
github:
  number: 242
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/242
```

## Background

The PR #241 linker symlinked `codespec.js`; that source is 0644, so execution fails with Permission denied. A temporary install wrapper is overwritten by the linker.

## Goal

- Write a 0755 wrapper (`#!/bin/sh exec <node> <source> "$@"`) for every `bin` entry; do not rely on the source executable bit.
- Test 0644 sources; rerun OpenSpec after deploy.

## Acceptance

- `codespec` executes; Runner tests/CI pass; production trace shows design/propose/apply.
