# 完成真实主子 Agent 轨迹端到端与降级验收

[English](m6-06-trace-e2e-validation.en.md) | 中文

```yaml flow
kind: issue
milestone: m6
priority: P0
status: ready
scope:
  - .agents/flow/issues/m6-06-trace-e2e-validation.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - runner/**
  - runner-contract/**
  - web/**
  - tests/**
  - plans/**
  - reports/**
  - docs/**
  - .github/workflows/eval-shard.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 95
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/95
```

## 背景

M6 的闭环必须证明真实 Claude Code 原生文件能经过 Runner、OSS、导入器、API 和 Web 完整呈现，而不是只依赖合成 fixture。还要证明重试、重复回调、格式升级和权限边界不会破坏历史 attempt 或评分结果。

## 目标

- 在固定 Runner 上执行包含主 Agent 与多个子 Agent 的真实用例，验证多文件上传、归一化、会话树、waterfall、span 详情和受控 raw 下载全链路。
- 验证失败重试产生独立 attempt，重复上传/完成回调与重复导入保持幂等，parser 升级可从原件重放且旧结果可追溯。
- 验证子文件缺失、未知格式和仅 stdout 的历史数据能够降级展示，不虚构主子关系，也不改变执行与评分终态。
- 补齐跨租户访问、raw 权限与审计、恶意正文安全和敏感信息检查，产出真实链路测试及冒烟证据。

## 验收

- 一次真实主子 Agent 用例可从用例详情跳到指定 attempt，页面展示与原生证据一致的会话关系、span 属性及最终用量，授权用户可下载对应原件。
- 重试后两个 attempt 的轨迹相互隔离，重复回调不增加 session/span，parser 重放不产生重复数据或覆盖原始文件。
- 缺文件、未知格式和历史 stdout-only 数据均显示明确降级与 raw 路径，评分结果保持可用，系统不宣称恢复不存在的原生关系。
- 权限、审计、XSS 和凭据泄露检查通过，测试报告如实记录环境、Runtime/parser 版本、证据及任何未覆盖项。
