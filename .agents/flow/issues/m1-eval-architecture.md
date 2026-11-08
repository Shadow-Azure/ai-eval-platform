# 定稿评测平台架构方案

[English](m1-eval-architecture.en.md) | 中文

```yaml flow
kind: issue
milestone: m1
priority: P0
status: in-progress
bootstrap: true
scope:
  - docs/**
  - research/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 3
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/3
```

## 背景

评测平台需求共十一条（登录、数据集、模型凭据、资源池、评测启动、多租户预留、实时进展、会话轨迹、前端轨迹查看、并发、CI/CD 部署），需先定稿架构再进入开发。

## 目标

- 完成业界开源方案与机制调研（评测框架、会话轨迹存储、GitHub Actions 资源池）。
- 形成候选架构并与用户讨论，定稿执行层、后端技术栈、轨迹 UI 与 MVP 范围。

## 验收

- `docs/design/eval-platform-architecture.md` 中英三件套落地并通过全部文档门禁。
- HTML 版架构文档随三件套交付，内容与 Markdown 版一致。
- 调研过程稿存于 `research/` 与 `reports/`，正式文档只记录定稿结论。
