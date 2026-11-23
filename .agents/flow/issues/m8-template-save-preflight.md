# [m8] 模板保存时校验镜像 digest，启动失败展示原因与修复建议

[English](m8-template-save-preflight.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - prototype/**
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - .agents/flow/issues/m8-node-download-network-retry.md
  - .agents/flow/issues/m8-node-download-network-retry.en.md
  - .agents/flow/issues/m8-node-download-network-retry.i18n.yaml
  - .agents/flow/issues/m8-template-save-preflight.md
  - .agents/flow/issues/m8-template-save-preflight.en.md
  - .agents/flow/issues/m8-template-save-preflight.i18n.yaml
adr: []
github:
  number: 398
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/398
```

## 背景

生产模板启动时报「启动前一致性检查未通过」，根因是数据集版本 `v2026.11-m825-prod` 与平台默认都没有执行/评分镜像 digest，但模板可以正常保存，用户只在启动时看到一句概括性错误。镜像 digest 的解析是确定性的（本次覆盖 → 数据集版本 → 平台默认），可以在保存模板时判定必然阻塞；启动失败时后端已返回 `details.blockers`，界面却只 toast 概括错误。

## 目标

- 原型在保存模板前展示镜像 digest 依赖：数据集版本与平台默认都为空时列出缺失的 execution/scoring digest、字段与修复建议，并阻止保存。
- 原型在启动被 blocker 阻断时逐条展示 blocker 的 code、原因、字段与修复建议，并说明没有创建评测；模板列表与详情标出「镜像待补」。
- 设计文档 §5.14 记录保存时校验的确定性边界，以及启动 blocker 的展示契约。
- 修改前后截图同视口、同主题并附在 issue #398 与原型 PR。

## 非目标

- 本原型 PR 不修改生产 `web/` 与 `backend/`；生产实现按 issue #398 的验收项随后跟进。
- 不改变 `prepare`/`create` 执行链路、启动接口契约或镜像 digest 的解析顺序。
- 不在保存时校验资产加载、容量与模型凭据等依赖实时状态的项。

## 验收

- 新建/更新模板选择「生产切片」v2026.11-m825-prod 时，保存前显示 2 项镜像 digest 缺失及修复建议；点击保存被拒绝，已填配置不丢失。
- 镜像 digest 就绪的数据集版本显示可保存，正常写入模板。
- 启动「生产验收 · 6×SWE-bench」时确认弹层提示阻断；确认后弹层逐条展示 blocker 原因、字段与修复建议，并说明未创建评测。
- 设计文档与原型 README 同步；`npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 与 `node prototype/build-standalone.mjs` 通过，PR CI 绿。

## 参考

- Issue：https://github.com/Shadow-Azure/ai-eval-platform/issues/398
- 设计：[docs/design/eval-platform-architecture.md](../../../docs/design/eval-platform-architecture.md)
