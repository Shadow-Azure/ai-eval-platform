# [m8-48] Contended spot pool warm pool and offline runner cleanup

English | [中文](m8-48-spot-warm-pool-cleanup.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/**
  - backend/**
  - docs/architecture.*
  - plans/**
  - tests/**
adr: []
github:
  number: 313
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/313
```

## Background

The #306 incident created 31 instances and recorded 29 interruptions in a two-hour window; the database held 307 offline runner rows at query time; a typical start-to-registration took 2–3 minutes, and the 60-second lost grace was too sensitive to a brief heartbeat jitter. The start/reclaim cost of zero-resident spot capacity was amplified.

## Goals

- Keep a small warm pool (`min_capacity≥1`) or reuse instances; the default `min_capacity=0` preserves the existing zero-resident behaviour.
- Extend the lost grace and probe before reclaiming, so a just-registered runner or a short jitter is not reclaimed.
- Periodically clean offline runner rows, and make the capacity controller explicitly ignore historical rows.
- Keep the cost cap and minimum capacity configurable without changing the default zero-resident behaviour.

## Approach

- Capacity demand combines queue demand with the warm-pool floor: `min_capacity` is a runner count, so the controller replenishes to that floor even with no queue, still bounded by `max_capacity`, the price threshold and the per-tick creation batch.
- Raise the `eval_spot_runner_lost_grace_seconds` default from 60 to 180 seconds, and require the runner to stay offline (or its heartbeat to exceed `heartbeat_timeout + grace`) before a reclaim, so a recovered runner is not reclaimed from one short jitter.
- Scale-down skips runners registered less than `eval_spot_runner_min_idle_seconds` (120 by default), so a just-registered runner is not drained; a runner that just finished a job is unaffected.
- The worker periodically deletes offline runner rows past the retention window: only rows with no active attempt and no non-terminal instance reference; terminal attempts and released instances keep their history but release the foreign-key pointer to the deleted row.
- The capacity controller filters runners with an explicit live-status allow-list plus a historical-status exclusion, so offline/draining rows never contribute to current/desired capacity.

## Acceptance

- With `min_capacity=1` and no queue, a warm instance is replenished; with `min_capacity=0` a pool still has zero instances when no job is waiting.
- A brief heartbeat jitter (longer than 60 seconds but within the new grace) no longer reclaims the instance; a real exit is still reclaimed after the grace expires.
- An idle runner registered less than 120 seconds ago is not reclaimed by scale-down; a runner that already ran a job still scales down as before.
- The cleanup task is idempotent and tested: expired offline rows are deleted, active/recent rows survive, and a second run deletes nothing.
- 300+ historical offline rows do not change capacity state; `npm test`, `pytest tests/backend`, `npm run verify:flow` and `npm run verify:docs` pass.
- Documentation records the warm pool, lost grace, cleanup retention and configuration knobs.

## Scope

- Involves: `backend/app/config.py`, `backend/app/services/capacity_control.py`, `backend/app/services/provisioning/aliyun.py`, `backend/app/services/runners.py`, `backend/app/worker/main.py`, `tests/backend/`, `docs/architecture.*`, `plans/m1-02-fullstack-design/`.
- Does not involve: fixed-pool behaviour, spot-to-on-demand/fixed fallback after repeated failures (#306 P2-1), provider 429s, or a new queue backend.
