# [m8-41] Fix model gateway token expiry in long spot tasks

English | [中文](m8-41-model-token-ttl.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-41-model-token-ttl.*
  - backend/app/api/runners.py
  - runner/scripts/real-task.mjs
  - runner/lib/mock-platform.mjs
  - tests/backend/test_runners.py
  - tests/runner/real-task-runtime-setup.test.mjs
adr: []
github:
  number: 269
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/269
```

## Background

In production 5×SWE-bench run `5f344310-4690-4625-ab5e-8f7632e82f20`, three cases (`astropy-13033/13236/13398`) ended with `401 token 已过期` in the agent stdout. The model gateway token TTL is 900 seconds, while the run has `case_timeout_seconds=3600` and `agent_timeout_seconds=1800`. The token is issued when the manifest is fetched, and the spot instance then spends 18–21 minutes pulling the SWE-bench image/starting the container, so the token expires before the agent makes its first model call.

Align token issuance and TTL with the agent execution budget: fetch the token after the image is ready and just before the agent starts; make the TTL cover the agent timeout (bounded by the case deadline) instead of a fixed 900 seconds.

## Goals

- The runner re-fetches the manifest/model token before the agent starts; image pull no longer consumes the token TTL.
- Manifest model-token TTL = `min(case_timeout, max(default 900, agent_timeout + 300))`; with a 1800-second agent timeout the token lives at least 2100 seconds.
- Backend and runner regression tests cover TTL calculation and refresh-at-agent-start.

## Acceptance

- The agent starts with a newly issued token; image pulls longer than 15 minutes no longer produce a 401.
- A 1800-second agent completes within the token TTL; the case deadline remains the hard cap.
- New tests pass; backend pytest and runner node tests pass.
