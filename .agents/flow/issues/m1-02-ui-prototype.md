# 设计评测平台原型 UI

[English](m1-02-ui-prototype.en.md) | 中文

```yaml flow
kind: issue
milestone: m1
priority: P0
status: ready
scope:
  - .agents/flow/issues/m1-02-ui-prototype.*
  - prototype/**
  - reports/**
adr: []
github:
  number: 6
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/6
```

## 背景

定稿架构已覆盖平台能力、数据流转与技术选型，但尚缺可直接评审的页面结构、交互路径与状态表现。开发前需要先形成统一界面基线，避免数据集导入、评测启动、实时观测和轨迹回看各自演进。

## 目标

- 覆盖登录、数据集与导入、模型凭据、资源连接与资源池、发起评测、进展与结果、会话轨迹查看等核心流程。
- 定义全局信息架构、导航、页面布局、表单交互和状态反馈，视觉语言与 React + Ant Design 保持一致。
- 交付使用模拟数据、可本地打开或运行的交互式原型，主路径可以连续点击完成。
- 记录页面与架构需求、数据实体及后续接口的映射，以及待确认的交互决策。

## 验收

- `prototype/` 下有可运行说明和入口，不依赖后端即可浏览全部主流程。
- 七个核心流程均有可点击页面，且覆盖空态、加载、失败、无权限、部分成功和危险操作确认等必要状态。
- 凭据掩码、实时评测进度、结果指标、主/子 Agent 会话树与 span 详情等关键信息表达清楚。
- 设计说明列出页面状态矩阵、流程映射和遗留问题，评审后不存在阻塞实现的 P0 交互问题。
