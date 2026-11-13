# [m8-21] CLI-generated assets: checkout the ref and run install/init at evaluation time

English | [中文](m8-21-source-mode-ref-selection.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-21-source-mode-ref-selection.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - backend/app/**
  - backend/migrations/versions/**
  - runner/**
  - web/**
  - tests/**
  - docs/**
  - plans/**
adr: []
github:
  number: 223
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/223
```

## Background

- The evaluation page already has assetRef (branch/tag/commit), and prepare resolves and scans by ref. But for CLI-generated assets the `init` still runs only in the load test (default branch), its output is frozen into the snapshot, and the evaluation injects that default-branch `.claude`.
- Command-less assets (SuperPower) use the “scan the repo’s native directories + freeze” path; changing the ref already works there, and this issue leaves that path unchanged.

## Goals

- Change only CLI-generated assets: at evaluation time `git clone` + `checkout commit` the selected ref, run the asset source’s install (source build) and init (generate `.claude`), then hand over to the agent; this class no longer injects the frozen snapshot.
- Keep command-less assets unchanged (load-test scan + frozen snapshot).
- Record repo/ref/commit, install/init output and the `.claude` inventory in the trace.

## Plan

### Flow
prepare ref → backend resolves the commit → payload with repo_url/commit + install/init → runner clone/checkout → install (source build) → init generates `.claude` → agent.

### Changes
| Layer | Change |
| --- | --- |
| Asset source | CLI-generated install_command becomes a source build (e.g. npm ci && npm run build); init_command stays codespec init |
| Backend | payload gains repo_url + resolved_commit; the class is marked source mode; command-less assets unchanged |
| Runner | clone/checkout/install/init for this class, skipping frozen injection; HOME uses /tmp/eval-home (m8-20) |
| Frontend | keep assetRef, add copy saying the ref will be checked out and rebuilt/re-initialized (prototype already shows it) |
| trace | record repo/ref/commit plus install/init output |

## Trade-offs

- init returns to evaluation time (CLI-generated only), reversing m8-11’s “no init at eval”; it runs as eval inside /testbed, so ownership stays consistent.
- Each evaluation adds a clone+install+build+init; shallow clone and dependency caching help.
- Private repositories use the platform Git credential, injected through the proxy and never written to the trace.

## Acceptance

- Switching branch/tag/commit for a CLI-generated asset checks out, builds and initializes that version and runs with its `.claude`.
- Command-less assets behave exactly as before; the trace includes repo/ref/commit and build logs.
