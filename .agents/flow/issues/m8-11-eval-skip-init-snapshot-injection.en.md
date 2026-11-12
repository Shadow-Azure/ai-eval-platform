# [m8-11] Skip codespec init at eval time and rely on the frozen snapshot

English | [中文](m8-11-eval-skip-init-snapshot-injection.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-11-eval-skip-init-snapshot-injection.*
  - .agents/flow/issues/m8-10-cli-generated-agent-assets.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - runner/scripts/real-task.mjs
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 204
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/204
```

## Background

- During production validation of m8-10, the evaluation Runner executed install + init inside the container; init created `.claude` as the `eval` user, then the host-side materialization of the frozen snapshot into the same `.claude` failed with `EACCES` (`.claude/agents`), so the agent never started and the evaluation failed.
- Decision: do not run init at evaluation time; run only install (so the CLI is available) and let the frozen snapshot inject `.claude`; init remains only in the asset-source load test that generates the assets and project state.
- This both avoids the ownership conflict and keeps the “evaluation uses the frozen snapshot” reproducibility semantics.

## Goals

- The Runner executes only `install_command` at evaluation time; a payload `init_command` is not executed.
- The frozen snapshot's `.claude` assets are materialized as before and chowned back to `eval`; no init output competes with the snapshot.
- An install failure aborts with `asset_runtime_setup_failed`; empty commands keep the existing no-setup behavior.
- Update the Runner tests: install runs, init does not, the snapshot wins, failures abort.
- Note: the evaluation workspace is no longer pre-initialized with `openspec/`; when needed, the agent or commands initialize it themselves.

## Acceptance

- No `.claude` ownership/overwrite conflict in the evaluation container; `.claude` comes from the frozen snapshot.
- The trace shows the CLI and the snapshot commands/skills available, and the agent can run the command flow from the prompt.
- Runner tests pass; `npm test` and CI pass.
- After deployment, re-run the production evaluation and confirm design→propose→apply executes.
