# 实现数据集与导入产品页面

[English](m5-01-dataset-pages.en.md) | 中文

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
  number: 33
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/33
```

## 背景

数据集导入目前只能靠 API 或 seed 数据，主路径必须能在 Web 上完成来源配置、校验、确认导入和用例浏览。

## 目标

- 实现数据集列表、详情、版本、用例和指标页面，复用原型的信息架构与设计令牌。
- 实现三步导入向导：来源配置、校验进度、确认生成版本，并展示部分失败。
- 接入服务端分页、筛选、搜索和错误态，支持返回与取消导入。
- 为关键组件和向导补充组件测试与视觉对照。

## 验收

- 用户可从页面完成一次导入并看到用例总数、指标定义和用例详情。
- 未登录、无权限、来源非法和部分失败都有明确页面状态。
- 页面在 1440×900 与原型对照一致，1280×800 下主操作可用。
- 关键交互有组件测试，错误不静默。
