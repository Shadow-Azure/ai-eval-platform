# [m8-53] Runner SIGTERM evidence and stop evidence

English | [中文](m8-53-runner-sigterm-observability.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-53-runner-sigterm-observability.*
  - .agents/flow/issues/m8-53-token-usage-stats.*
  - backend/app/services/provisioning/aliyun.py
  - runner/lib/diagnostics.mjs
  - runner/scripts/real-task.mjs
  - tests/backend/test_aliyun_provisioner.py
  - tests/sigterm-diagnostics.test.mjs
adr: []
github:
  number: 334
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/334
```

## Background

In production evaluation 2739aa67, astropy__astropy-13236 and astropy__astropy-13398 both failed with `runner_lost` / "Runner received SIGTERM"; the platform recorded no stop origin and cannot tell a cloud-side stop, system maintenance, manual stop, or a local Runner/VM exit. The runner emitted only one SIGTERM line without systemd/journal/dmesg/metadata, and the instance was released before any evidence could be collected.

The first PR revision made `runner_lost` retryable, but the user pointed out that the current phenomenon is clearly not low-probability: retrying before the root cause is known only burns more instances and time. This issue therefore withdraws automatic retry and focuses on capturing the missing evidence; retry is deferred until the root cause is confirmed unavoidable and low-probability.

## Goals

- On SIGTERM, emit structured diagnostics (systemd/journal/dmesg/metadata/GitHub env) and upload `diagnostics/sigterm.json` as `artifact_kind=artifact`.
- On reclaim, record `OperationLocks`, instance status/age, spot duration/termination time, attempt phase/start/end, case key and github_run_id in `provisioned_instance.cost_json["stop_context"]` and `resource_event`; no new columns or migrations.
- Keep the existing retry semantics; this issue introduces no blanket retry.
- Leave fields/interfaces for follow-up GitHub job evidence, CloudMonitor/ActionTrail, metadata polling and UI alerts.

## Acceptance

- The SIGTERM handler prints `{"status":"sigterm_diagnostics",...}`; the uploads callback contains `diagnostics/sigterm.json` (artifact_kind=artifact); the failure callback is still sent.
- The failed instances' `stop_context` includes `operation_locks`, `instance_status`, `instance_age_seconds`, `attempt_phase`, `attempt_started_at`, `case_key`, `github_run_id`; `resource_event.payload_json.stop_context` mirrors them; no migration.
- `handle_failed` retry semantics stay unchanged; `runner_lost + retryable=false` still settles from the Runner's field and is not automatically retried by this issue.
- `pytest tests/backend`, `npm test`, `npm run verify:flow`, `npm run verify:docs` pass.
- Non-goals: automatic retry, GitHub job API, CloudMonitor/ActionTrail polling, metadata preemption watch, UI/alerting; split into follow-up issues.
