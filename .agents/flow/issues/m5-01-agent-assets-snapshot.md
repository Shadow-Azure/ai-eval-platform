# 实现 Agent 资产源、不可变快照与 Runtime 注入

[English](m5-01-agent-assets-snapshot.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P0
status: ready
scope:
  - .agents/flow/issues/m5-01-agent-assets-snapshot.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - runner/**
  - tests/**
  - docs/**
  - package.json
  - package-lock.json
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 83
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/83
```

## 背景

目标的评测组合是「Runtime × Agent 资产源 × 模型 × 资源池」，但当前 `/assets` 仍是占位页，评测创建只有 `asset_snapshot_json` 空字段，Runner 也没有消费资产快照。用户无法在页面上配置 Skill、Command、Subagent，真实评测只能使用 Runtime 内置能力。

## 目标

- 建立 Agent 资产源、不可变快照与资产记录，保存 Git 来源、凭据、默认 Ref、扫描路径、选择方式和资产类型。
- 实现 Ref 解析、目录扫描和资产识别，Skill 以 `SKILL.md` 目录为单位，Command 与 Subagent 按 Runtime 原生格式识别，无法判断时必须显式选择。
- 将选中资产冻结为不可变 OSS 包，记录 resolved commit、tree digest、文件摘要和版本，发起评测的所有用例共享同一快照。
- 在 Runner 中消费快照并按 Runtime 映射到 `.claude/`，同时实现资产源管理、资产树选择、兼容性提示和快照详情页面。

## 验收

- 用户可以在页面创建资产源、解析 Ref、扫描资产并选择完整目录或指定路径，刷新和重新打开后结果一致。
- 同一资产源在评测启动后切换分支或删除文件，不影响已启动评测，评测记录可以追溯 requested_ref、resolved_commit 和文件摘要。
- Claude Code 只加载快照内选中的 Skill、Command 与 Subagent，不读取宿主环境中的其他资产，不支持的资产类型有明确降级提示。
- 重复解析和重复打包具有幂等性，资产内容与凭据不进入日志，后端、Runner、前端和资产 golden fixture 测试通过。
