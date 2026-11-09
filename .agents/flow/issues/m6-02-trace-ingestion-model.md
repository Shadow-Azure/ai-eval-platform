# 建立版本化轨迹模型与幂等导入流水线

[English](m6-02-trace-ingestion-model.en.md) | 中文

```yaml flow
kind: issue
milestone: m6
priority: P0
status: ready
scope:
  - .agents/flow/issues/m6-02-trace-ingestion-model.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - tests/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 91
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/91
```

## 背景

控制面已经保存 attempt 级对象清单和 `trace_status`，但尚没有 `trace_file/session/span` 模型，也没有读取 OSS 原件并写入派生轨迹的导入流水线。M6 需要让解析可以重放、失败可以降级，同时不阻塞已经完成的评分结果。

## 目标

- 建立带租户与 attempt 归属的 `trace_file/session/span` 表，保存原生标识、父子证据、Runtime/格式/parser 版本、顺序、时间、用量、摘要与大内容对象引用。
- 在完成回调后触发异步导入，校验对象 size 与 sha256，按固定 Runtime 适配器解析，并在事务内替换对应派生树。
- 以文件 digest 和 parser_version 保证重复回调幂等，支持从同一 OSS 原件重放升级版解析器，而不复制 session 或 span。
- 提供 ready、degraded、failed 等可查询状态及脱敏错误摘要，发出轨迹变更事件；单文件或单记录失败不影响其他文件和评分结果。

## 验收

- 同一文件与 parser_version 重复导入后记录数不变，parser 升级重放能完整替换派生树并保留原件与版本信息。
- 子文件缺失、未知格式或单行损坏时产生明确降级状态和文件/行号摘要，其余可解析轨迹仍可查询，敏感原始行不进入错误日志。
- 导入失败不改变评分与执行终态，轨迹状态变化能通过事件通知前端刷新。
- 数据库迁移可从空库和上一版本执行，后端测试覆盖事务回滚、并发重复导入、对象校验和重放。
