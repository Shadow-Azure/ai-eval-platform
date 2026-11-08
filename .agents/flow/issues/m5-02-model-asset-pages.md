# 实现模型、凭据与 Git 资产页面

[English](m5-02-model-asset-pages.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: backlog
scope:
  - .agents/flow/**
  - web/**
  - backend/**
  - tests/**
  - package.json
  - package-lock.json
  - docs/**
  - plans/**
  - reports/**
adr: []
github:
  number: 34
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/34
```

## 背景

模型 Key、临时凭据和 Git 资产是发起评测的必需输入，用户需要在页面上安全配置、校验并看到不可变快照。

## 目标

- 实现模型配置列表、新增、编辑和连通性测试，Key 只写不读，支持轮换和撤销。
- 建立 agent_asset_source、snapshot、asset 表与 API，覆盖 Git 源、Ref 解析、扫描和文件树。
- 实现资产页面，展示 Skill、Command、Subagent 及 Runtime 兼容性提示。
- 确保临时 Key 不进入 URL、本地存储、日志或查询缓存。

## 验收

- 保存后不回显明文 Key，轮换使旧凭据失效，历史评测仍可读取掩码。
- Ref 解析为不可变 commit，扫描结果可复现并支持树形选择。
- 不支持或降级的资产类型有显式提示，不静默忽略。
- 页面与原型一致，关键表单和错误态有测试。
