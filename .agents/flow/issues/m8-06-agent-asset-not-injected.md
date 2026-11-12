# [m8-06] 前端向导发起的评测未注入 Agent 资产

[English](m8-06-agent-asset-not-injected.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
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

## 背景

2026-11-12 在生产环境配置 SuperPower（obra/superpowers）资产源后，用前端「发起评测」向导启动评测 20bff639。评测完成并 resolved，但该用例轨迹的 skill_listing 只有 Claude Code 内置 skill，没有 15 个 Superpowers skill；评测配置页同时显示「Agent 资产 15 项 / 0 个文件」与「包摘要 — · 0 B」，Runner 日志也没有 assets_materialized。同一资产快照改由 POST /api/v1/evaluations 直连接口（带 asset_snapshot_id）启动时，Runner 日志输出 assets_materialized files=80，配置页显示 15 项 / 80 个文件 / 223333 B，说明资产包与 Runner 注入能力本身正常。

## 原因

前端向导走 prepare→create 路径，冻结的资产快照在两层被裁剪：preflight 生成的 asset_snapshot 使用 id 而非 snapshot_id，且不含 bundle_key（backend/app/services/preflight.py:405-427）；preparations._snapshot_asset 只保留 id/source_id/requested_ref/resolved_commit/tree_digest/asset_count/runtime_key/assets，进一步丢掉 bundle_digest/bundle_size/file_count（backend/app/services/preparations.py:293-305），并以该 JSON 写入 eval_run.asset_snapshot_json（:784）。Runner 任务包由 task_asset_payload 从该 JSON 生成，没有 bundle_key 就不会签 bundle_url（backend/app/services/agent_assets.py:1198-1210）；runner/lib/asset-bundle.mjs:114 在缺少 snapshot_id 时直接返回空列表且不报错，因此 .claude 下没有写入任何资产，Claude Code 只加载内置 skill。

## 目标

- prepare 冻结的资产快照保留 snapshot_id、bundle_key、bundle_digest、bundle_size 与 file_count，使前端向导创建的评测与直连接口一致地携带资产包。
- Runner 在声明了资产（asset_count>0）却缺少 snapshot_id 或 bundle_url 时显式失败，不再静默跳过注入。
- 无资产（asset_count 为 0）的评测行为保持不变。

## 验收

- 后端测试覆盖 prepare 响应、consume_preparation 写入的 asset_snapshot_json 以及 Runner 任务包，均含 snapshot_id/bundle_key/bundle_digest/bundle_size/file_count，并能签出 bundle_url。
- Runner 测试覆盖 asset_count>0 且缺 snapshot_id 时抛出 asset_snapshot_missing，asset_count 为 0 时仍返回空列表。
- 生产复测：前端向导创建的评测在 Runner 日志出现 assets_materialized，轨迹 skill_listing 含 Superpowers skill。
- backend 与 runner 相关测试通过。
