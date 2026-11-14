# [m8-31] 发起评测可选最大轮次（默认 200，前端可配）

[English](m8-31-eval-max-turns.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-31-eval-max-turns.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - prototype/app.js
  - prototype/index.html
adr: []
github:
  number: 246
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/246
```

## 背景

`--max-turns` 目前是 Runner 级默认值；复杂 command 流（design→propose→apply，apply 用 subagent）会因轮次耗尽提前结束。需要在发起评测时可选配置。

## 目标

- 前端评测向导 Step 3 新增可选“最大轮次”，留空默认 200，可修改；范围 1–1000。
- Step 4 摘要展示默认/用户值；轮次语义为一次模型响应（可能包含多个工具调用）。
- 后续实现：API/评测快照持久化、Runner 透传、任务详情展示。

## 本轮范围

- 仅原型：`prototype/app.js` + 重建 `prototype/index.html`。
- Before/After 截图已发 Feishu 并写入 issue #246。

## 验收

- 原型 Step 3 展示可选输入与默认值提示；Step 4 展示生效值。
- 完整实现另行提交（API/后端/Runner/测试/文档）。
