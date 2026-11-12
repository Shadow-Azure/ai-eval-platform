# [m8-06] Wizard-launched evaluations do not inject agent assets

English | [中文](m8-06-agent-asset-not-injected.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-06-agent-asset-not-injected.*
  - .agents/flow/issues/m8-03-eval-records-pagination.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - backend/app/services/preflight.py
  - backend/app/services/preparations.py
  - runner/lib/asset-bundle.mjs
  - tests/backend/test_preflight.py
  - tests/backend/test_preparations.py
  - tests/runner/asset-bundle.test.mjs
  - plans/**
adr: []
github:
  number: 187
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/187
```

## Background

After configuring the SuperPower (obra/superpowers) asset source in production on 2026-11-12, evaluation 20bff639 was launched through the frontend evaluation wizard. The run completed and was resolved, but the case trace's skill_listing contained only Claude Code built-in skills and none of the 15 Superpowers skills; the evaluation configuration page also showed "Agent assets 15 items / 0 files" and "Bundle digest — · 0 B", and the runner log had no assets_materialized. When the same asset snapshot was launched through the direct POST /api/v1/evaluations API with asset_snapshot_id, the runner log printed assets_materialized files=80 and the configuration page showed 15 items / 80 files / 223333 B, proving the bundle and the runner's injection path work.

## Cause

The frontend wizard uses the prepare→create path, and the frozen asset snapshot is trimmed in two layers: preflight builds asset_snapshot with id instead of snapshot_id and without bundle_key (backend/app/services/preflight.py:405-427); preparations._snapshot_asset keeps only id/source_id/requested_ref/resolved_commit/tree_digest/asset_count/runtime_key/assets and further drops bundle_digest/bundle_size/file_count (backend/app/services/preparations.py:293-305), writing that JSON to eval_run.asset_snapshot_json (:784). The runner task payload is built from that JSON by task_asset_payload, which presigns no bundle_url without bundle_key (backend/app/services/agent_assets.py:1198-1210); runner/lib/asset-bundle.mjs:114 returns an empty list without snapshot_id and raises no error, so nothing is written under .claude and Claude Code loads only built-in skills.

## Goals

- Keep snapshot_id, bundle_key, bundle_digest, bundle_size and file_count in the prepared asset snapshot so wizard-launched evaluations carry the asset bundle like direct API launches.
- Make the runner fail explicitly when assets are declared (asset_count>0) but snapshot_id or bundle_url is missing, instead of silently skipping injection.
- Keep asset-free evaluations (asset_count 0) unchanged.

## Acceptance

- Backend tests cover the prepare response, the asset_snapshot_json written by consume_preparation and the runner task payload: all carry snapshot_id/bundle_key/bundle_digest/bundle_size/file_count and can presign bundle_url.
- Runner tests cover asset_count>0 without snapshot_id raising asset_snapshot_missing, and asset_count 0 still returning an empty list.
- Production re-check: a wizard-launched evaluation shows assets_materialized in the runner log and Superpowers skills in the trace skill_listing.
- Relevant backend and runner tests pass.
