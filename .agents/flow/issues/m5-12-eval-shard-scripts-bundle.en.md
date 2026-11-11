# Remove eval-shard checkout dependency with a content-addressed scripts bundle

English | [中文](m5-12-eval-shard-scripts-bundle.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: done
scope:
  - .agents/flow/issues/m5-12-eval-shard-scripts-bundle.*
  - .agents/flow/milestones/m5-product-workflows.*
  - .github/workflows/eval-shard.yml
  - .github/workflows/deploy.yml
  - backend/app/api/runners.py
  - backend/app/config.py
  - backend/app/services/**
  - scripts/**
  - runner/**
  - runner-contract/**
  - tests/**
  - docs/**
  - deploy/**
adr: []
github:
  number: 134
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/134
```

Flow ID: M5-12 (GitHub #134)

## Background

In a production retest, the eval-shard execute job failed in `actions/checkout` on an Aliyun spot Runner (run 42940436677). The job failed before `real-task.mjs`, so no `started` callback was sent; the platform attempt stayed queued until the 3600-second `queue_timeout`. A prepare failure strands every case in the shard; an execute failure strands one case.

PR #133 added three step-level checkout retries. It lowers the failure probability but keeps the GitHub clone on the critical path, and the platform still has no signal when the job fails; it only settles at the 3600-second mark.

## Symptoms

- The platform case/attempt shows queued for a long time while the GitHub job is already failed; the two views disagree.
- Production run 42940436677: dispatched at 01:38:39Z, execute job started at 01:43:27, checkout began at 01:43:33; three fetch attempts failed at 01:45:03, 01:48:09 and 01:50:32 (GnuTLS reset, curl 28, packfile), and the job failed at 01:50:38; the platform settled only at 02:38:39Z.
- The platform does not watch workflow-run failures; manual retry requires the run to still be running, and a terminal run requires a new evaluation.

## Proposal

Turn the platform scripts needed by prepare/execute into a content-addressed release artifact, downloaded through the platform proxy instead of checking out the platform repository:

1. At release time, package `runner/` and `runner-contract/schemas/` (`contracts.mjs` reads the schema directory one level up).
2. Build a deterministic tar/gzip, compute md5 (also record sha256), and name it `eval-runner-scripts-<md5>.tar.gz`.
3. Check OSS for `runner/scripts/<name>`; reuse it when present and plausibly sized, otherwise upload and verify.
4. Add a `/runner/v1/scripts/<name>` proxy that streams the private OSS object; authorize it with a short-lived token bound to eval/shard/attempt.
5. Download, verify and extract the bundle into `$RUNNER_TEMP/eval-scripts`; run contracts, plan-cases and real-task from the bundle.
6. Keep a checkout fallback during migration: when the bundle succeeds, skip checkout; when the bundle fails, fall back to checkout; remove the fallback after E2E.

Reuse the deploy OSS cache job from PR #129 and the fixed proxy endpoint from PR #132.

## Tasks

| Task | Content | Owner |
|---|---|---|
| Artifact | Add bundle build/cache to deploy; inject name/md5; add runner/runner-contract trigger paths | Code |
| Proxy and auth | Scripts proxy, companion metadata endpoint, short-lived token | Code |
| Workflow | Remove prepare/execute checkout; download/verify/extract; keep fallback | Code |
| Failure signal | Dispatch preflight for the bundle; job failure callback or workflow_run polling | Code |
| Verification | Local determinism/proxy tests; Aliyun 5-case E2E | Human + Code |

## Acceptance

- Two builds of the same source have the same md5; the bundle contains runner + schemas and contracts/plan-cases run after extraction.
- prepare/execute no longer contain `actions/checkout`; 5 cases at concurrency 5 all start and report back.
- A missing bundle or a 5xx proxy surfaces within minutes, without waiting for the 3600-second queue_timeout.
- Checkout is skipped when the bundle succeeds; fallback fires only when the bundle fails and leaves an alert.
- PR #133 remains a temporary mitigation; the final path does not depend on a GitHub clone.

## Risks and non-goals

- Non-goals: do not change the case target repository or dataset image; do not make the OSS bucket public-read.
- Risks: md5 detects corruption, not tampering (pair it with sha256); token expiry/scope; version skew between workflow HEAD and the deployed commit; stale bundle cleanup.
- Fallback is a migration safety net, not the long-term architecture; a prolonged bundle outage still degrades to the GitHub clone.

## References

- PR #133 (checkout retries, OPEN)
- PR #129 (runner tarball cached in OSS, MERGED)
- PR #132 (runner tarball proxy endpoint, MERGED)
- Issue #119 (M5-08 production runner callback)
- run 42940436677
