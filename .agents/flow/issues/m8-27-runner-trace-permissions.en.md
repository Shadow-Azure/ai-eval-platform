# [m8-27] Fix Runner trace directory permissions and preserve Agent failure diagnostics

English | [中文](m8-27-runner-trace-permissions.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-27-runner-trace-permissions.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/scripts/real-task.mjs
  - tests/runner/trace-permissions.test.mjs
adr: []
github:
  number: 238
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/238
```

## Background

`real-task.mjs` chmods the workdir recursively before creating `workdir/trace`. The trace directory mode therefore depends on the runner process umask (0022 -> 0755) and owner. The agent runs as `eval`; when it fails, the runner's attempt to write `trace/diagnostics` gets EACCES and hides the real agent stderr. The OpenSpec source-mode 34s failure therefore only shows `agent exit 1`.

## Goal

- Create `.runtime-home` and `trace` before the recursive chmod, or chmod them 0777 explicitly; do not depend on umask/owner.
- Add a regression test for creation order and non-root writability.
- After deploy, rerun OpenSpec source-mode and capture the real agent stderr.

## Acceptance

- `mkdirSync(trace)` precedes `chmod -R a+rwX workdir`; test covers it.
- Failure diagnostics do not lose agent stderr to trace directory permissions.
- Runner CI passes; production rerun exposes the real Claude error.
