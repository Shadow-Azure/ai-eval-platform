# [m8-22] CLI-generated assets: checkout by ref and run install/init at evaluation time

English | [中文](m8-22-cli-source-checkout.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-22-cli-source-checkout.*
  - .agents/flow/issues/m8-21-source-mode-ref-selection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/**
  - backend/migrations/versions/**
  - runner/**
  - web/**
  - prototype/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: 225
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/225
```

## Problem

- The evaluation page already has assetRef (branch/tag/commit), and prepare resolves and scans by ref; for command-less assets (SuperPower) changing the ref already works.
- But for CLI-generated assets (CodeSpec) the `init` still runs only in the load test (default branch) and its output is frozen into the snapshot; the Runner only injects that frozen `.claude` plus the runtime install. So after changing the branch, install uses the new ref but `.claude` is still the default-branch copy — the switch does not affect the flow.

## Plan

Change only CLI-generated assets: at evaluation time fetch the selected ref and check out the resolved commit, run the asset source’s install (source build) and init (generate `.claude`), then hand over to the agent; this class no longer injects the frozen snapshot. Command-less assets stay as they are.

The platform builds a source archive for the resolved commit when assets are resolved and stores it in object storage; evaluation receives a short-lived presigned URL. Extracting it is equivalent to `git clone` + `git checkout <commit>`. This keeps long-lived platform Git credentials off the Runner host while private repositories are still read with the platform credential.

### Flow
prepare ref → backend resolves the commit → source archive frozen during asset resolution → payload with repo_url/commit + install/init + short-lived source URL → runner downloads and extracts → install (source build) → init generates `.claude` → selected static repository assets are injected → agent.

### Changes
| Layer | Change |
| --- | --- |
| Asset source | CLI-generated install_command becomes a source build (e.g. `npm ci && npm run build && npm install -g .`); init_command stays a command such as `codespec init`; the load test also runs inside the checkout for the ref |
| Backend | snapshot freezes `source_mode`, `repo_url`, `source_archive_*`; payload carries `source_mode` / `repo_url` / short-lived `source_archive_url`; command-less unchanged |
| Runner | CLI-generated assets extract the source, run install/init, inject selected static assets and verify they exist, skipping the frozen `.claude`; HOME stays `/home/eval` (m8-19) |
| Frontend | keep assetRef, add copy that the ref will be checked out and rebuilt/re-initialized; align the default-branch and load-test explanation |
| trace | record repository/ref/commit, install/init output and the static asset inventory (no credentials) |

## Frontend prototype

Before (“default Ref”, “evaluation Ref”) and after (“default branch”, “Agent version (branch / tag / commit)”) screenshots are in PR #224 and embedded in issue #223. This issue implements the same copy in the Web app and uses `prototype/` as the UI baseline.

## Trade-offs

- init returns to evaluation time (CLI-generated only), reversing m8-11’s “no init at eval”; it runs as eval inside /testbed, so ownership stays consistent.
- The platform downloads and stores one source archive per resolved commit; each evaluation adds extraction + install/build + init. Archives are keyed by commit to avoid duplicate storage.
- The Runner does not clone: Runner hosts do not keep long-lived credentials (architecture constraint), so the platform reads the source and hands over only a short-lived URL. Private credentials never enter the trace.
- The source archive is capped at 128 MiB; exceeding it fails resolution with an explicit error instead of silently falling back to the default-branch snapshot.

## Acceptance

- Switching branch/tag/commit for a CLI-generated asset checks out, builds and initializes that version and runs with its `.claude`.
- Command-less assets are unchanged; the trace includes repository/ref/commit and build logs.
- The load test runs in the same checkout; private-repository load tests reuse the scan/create token.
