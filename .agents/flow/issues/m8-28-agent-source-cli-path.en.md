# [m8-28] Include source CLI directories in the Agent PATH so /opsx commands run

English | [中文](m8-28-agent-source-cli-path.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-28-agent-source-cli-path.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - runner/adapters/runtimes/claude-code.mjs
  - tests/runner/agent-source-path.test.mjs
adr: []
github:
  number: 240
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/240
```

## Background

After the trace permission fix, `/opsx:design` expands but the design skill's `codespec` CLI returns `command not found`; the Agent improvises and the design→propose→apply flow does not actually run.

## Root cause

`agentScript()` only adds conda testbed and `/opt/node/bin` to PATH; source-mode directories are only on the install/init PATH. The CLI file is `codespec.js`, with no bare `codespec` command.

## Goal

- Add source CLI directories to the Agent PATH.
- Create a stable `codespec` -> `codespec.js` link under `/asset-source/bin` when present.
- Lock with a runner test; after deploy rerun OpenSpec and confirm design→propose→apply.

## Acceptance

- `agentScript()` PATH includes source CLI directories and the codespec link.
- Runner CI passes; production trace shows codespec/design stages, not command not found.
