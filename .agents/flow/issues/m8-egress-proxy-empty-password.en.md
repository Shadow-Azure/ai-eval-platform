# [m8] Egress proxy rejects empty Basic password with 407: runner sends a non-empty placeholder and regression coverage

English | [中文](m8-egress-proxy-empty-password.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-egress-proxy-empty-password.*
  - runner/scripts/real-task.mjs
  - tests/runner/**
  - tests/deploy/**
  - deploy/proxy/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - plans/**
adr: []
github:
  number: 442
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/442
```

## Background

Since 2026-11-26, evaluations that download GitHub assets through the egress proxy fail at the install stage with 407 (`asset_source_install_failed` / E407), so candidate evaluations never reach the agent. The runner puts the egress token in the HTTP Basic username slot and leaves the password empty; Squid answers 407 for an empty password and never calls the auth helper. The same failure reproduces across multiple self-evolution runs; the full diagnosis and controlled experiment are in issue #442. The production impact is P0; the flow priority is recorded as P2 to respect the parallel rules under m8's current highest unfinished tier (P1).

## Goals

- `credentialedEgressProxy()` in `runner/scripts/real-task.mjs` sends a non-empty placeholder password, producing `http://<token>:x@<host>:<port>`.
- Keep the runner/proxy contract comments and the Chinese/English `docs/architecture` examples in sync with "token in the username slot, non-empty placeholder in the password slot".
- Add regression tests: assert the generated proxy URL has a non-empty password; the helper still verifies only the username token when a non-empty placeholder password is sent.

## Acceptance

- With a token, `HTTPS_PROXY` / `HTTP_PROXY` for both asset install and agent exec are `http://<token>:x@<host>:<port>` with a non-empty password; the old empty-password form fails the tests.
- Without a token the credential-free private-listener URL is injected as before.
- `node --test tests/runner/real-task-runtime-setup.test.mjs`, `node --test tests/deploy/egress-proxy.test.mjs`, `npm test`, `npm run lint:docs` and `npm run verify:flow` pass; PR CI is green.
- Issue #442 already verified empty password 407 and non-empty password 200 against real Squid; this PR locks the URL shape with unit tests and production acceptance re-runs a real asset install after deploy.
