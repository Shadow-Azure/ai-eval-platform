# [m8-414] 资源池 Runner 标签唯一化与派发隔离

[English](m8-414-pool-unique-runner-labels.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-414-pool-unique-runner-labels.*
  - backend/**
  - docs/**
  - infra/runner/bootstrap.sh
  - plans/**
  - reports/**
  - tests/**
  - web/src/api/resources.ts
  - web/src/pages/ResourcesPage.tsx
adr: []
github:
  number: 414
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/414
```

## 背景

平台用资源池做逻辑隔离，但 GitHub Actions 只按 Runner 标签匹配。fixed 池沿用全局 `self-hosted,linux,eval`，spot Runner 同时拥有这三个标签和池标签，于是 fixed 作业可以落到 spot Runner 上：fixed 评测占用 spot 容量，spot 评测反而排队；页面和 run 快照仍显示 fixed 池，容量核算也失真。

## 目标

- 每个池拥有显式且唯一的 `runs_on` 标签集，注册与派发共用同一列表。
- 池存在时禁止回退全局默认标签；缺失标签在创建、更新与启动预检中被拒绝。
- 历史 `{"kind": "fixed"}` 池由迁移回填唯一标签；固定 Runner 的 bootstrap 默认注册池唯一标签。
- 回归测试证明 fixed 作业标签与 spot Runner 标签不构成子集。

## 方案

- `runner_labels` 只接受池显式标签：池存在但没有 `runs_on` 时抛 `RunnerLabelsMissing`，只有无池 synthetic run 使用默认三件套；删除 `LEGACY_FIXED_LABELS` 回退。
- 资源池创建/更新校验 `runs_on` 非空、包含 `self-hosted`、包含至少一个非默认的池唯一标签，并拒绝与另一池标签集构成子集或超集。
- 标签统一存为 `labels_json.runs_on`；控制台写入的标签映射与 `instance_config.runs_on` 在保存时归一化。
- 迁移 0034 为历史池回填 `runs_on`，按池名派生唯一标签，必要时追加 id 短标签打破重叠。
- `ensure_fixed_pool` 写入 `self-hosted,linux,eval,pool-fixed-linux`；固定 Runner bootstrap 默认追加 `pool-fixed-linux`，重注册时生效。
- 启动预检检查池标签缺失、在线 Runner 缺少池标签以及与另一池标签重叠；fixed 池无兼容 Runner 时阻断，spot 池旧 Runner 仅警告。
- 前端资源池表单把 Runner 标签设为必填，列表与详情按 `runs_on` 展示标签。

## 验收

- fixed 池评测的 execute job 只匹配该池 Runner，不再匹配任何 spot Runner。
- spot 池评测的 execute job 只匹配 spot 池 Runner。
- 新建/更新池缺少唯一 `runs_on` 或与其他池重叠时返回 422；历史池迁移后具备唯一标签。
- fixed/spot 标签子集回归测试在修复后通过；`pytest tests/backend`、`npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。

## 范围

- 涉及：资源池标签解析与派发、资源池 API 校验、启动预检、Alembic 迁移、固定 Runner bootstrap、资源池前端标签展示与表单、测试与架构文档。
- 不涉及：跨评测 `pool_in_flight` 预留导致的 `effective_concurrency=0`、竞价供给器实现、Runner 执行逻辑。
