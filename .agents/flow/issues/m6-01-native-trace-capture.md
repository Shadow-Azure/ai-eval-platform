# 补齐原生轨迹采集与多文件清单契约

[English](m6-01-native-trace-capture.en.md) | 中文

```yaml flow
kind: issue
milestone: m6
priority: P0
status: ready
scope:
  - .agents/flow/issues/m6-01-native-trace-capture.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - runner/**
  - runner-contract/**
  - backend/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 90
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/90
```

## 背景

M3 已在 Runtime Adapter 中实现原生 JSONL 发现，但真实任务当前只上传 `trace/claude-stream.jsonl`，完成回调也只承载单个 `trace_manifest`。主 Agent 的原生会话、子 Agent 文件和 `.meta.json` 没有完整归档，后续无法凭原生证据恢复主子关系。

## 目标

- 完整采集主 Agent、子 Agent 的原生会话文件及关系元数据，stdout/stderr 作为独立诊断产物保存，执行失败时也保留已产生的轨迹。
- 以向后兼容方式扩展上传与完成回调契约，支持按 attempt 绑定多文件清单，记录安全逻辑路径、digest、size、content type、Runtime 与原生格式版本。
- 对象键仍由平台生成，Runner 不能指定任意前缀；旧单文件 manifest 继续可读，缺少原生文件时明确标记不完整采集。
- 补齐主 Agent、主子 Agent、失败执行和重试 attempt 的 Runner 契约及采集测试，为异步归一化提供稳定输入。

## 验收

- 一次包含子 Agent 的真实 Claude Code 执行能上传全部原生会话与关系元数据，清单可追溯到具体 case attempt，且不泄露宿主绝对路径。
- 同一文件重复上报不产生歧义，重试生成独立 attempt 清单，旧 attempt 的对象与清单不被覆盖。
- 旧 `trace_manifest` 数据仍能处理；只有 stdout 或部分原生文件时显示采集不完整，不伪装为完整轨迹。
- Runner、后端和 Schema 测试覆盖多文件、重复 digest、非法路径、失败保留与新旧契约兼容。
