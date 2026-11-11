# 实现 Claude Code 轨迹归一化与主子关系恢复

[English](m6-03-claude-code-trace-normalizer.en.md) | 中文

```yaml flow
kind: issue
milestone: m6
priority: P0
status: done
scope:
  - .agents/flow/issues/m6-03-claude-code-trace-normalizer.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 92
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/92
```

## 背景

统一模型只有接入首个真实 Runtime 解析器才有诊断价值。Claude Code 的主子关系分布在 Agent tool call、`.meta.json`、agentId 和侧链文件中，`parentUuid` 又只表达消息树；若按时间或 sessionId 猜测，会把并行 Agent、fork 或 resume 错挂。

## 目标

- 实现显式版本化的 Claude Code 原生文件解析器，以最终原生文件为权威，而不是仅解析 headless stdout。
- 按 Agent tool_use、`.meta.json` toolUseId、toolUseResult/agentId 与子文件证据链恢复主子 session；无法证实的条目进入 unlinked 或 degraded。
- 归一化 AGENT、LLM、TOOL、MESSAGE、THINKING 等 span 及其层级、顺序、时间、状态、标准属性、内容预览和对象引用，token 与成本采用最终结果而非 delta 累加。
- 建立固定 Runtime 版本的脱敏 golden fixtures，覆盖单主 Agent、多子 Agent、fork/resume、重复 delta、缺失子文件、未知版本和大工具输出。

## 验收

- 主子关系只由原生证据建立，`parentUuid` 仅用于消息/span 层级，sessionId 相同不会被视为父子证据，时间相邻也不会触发自动挂载。
- 并行子 Agent 的顺序、归属和最终用量准确，重复消息不重复入库，缺失 parent 的 span 作为可识别的 root fragment 展示。
- 子文件缺失或格式未知时保留可浏览部分与 raw 入口，不启动猜测解析；大正文只存截断预览和受控引用。
- Golden fixture 与后端测试覆盖上述场景，并验证属性命名、错误摘要、脱敏和恶意文本不会形成可执行内容。
