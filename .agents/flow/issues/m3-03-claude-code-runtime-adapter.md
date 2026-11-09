# Claude Code Runtime 适配器与执行镜像

[English](m3-03-claude-code-runtime-adapter.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: done
scope:
  - .agents/flow/issues/m3-03-claude-code-runtime-adapter.*
  - backend/**
  - runner/**
  - infra/**
  - tests/**
adr: []
github:
  number: 47
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/47
```

## 背景

M3 指定 Claude Code Runtime。设计文档 07 §4 定义了 RuntimeAdapter 协议与资产映射（Skill/Command/Subagent 到 `.claude/`），启动命令模板是平台审核代码，不接受 UI 注入任意 shell，参数走结构化变量绑定。轨迹以最终原生文件为权威（决策 D8），解析与父子关系归 M6，M3 只负责收集落盘。Runner 使用隔离的 Runtime HOME 与允许清单控制资产加载面，不能简单加 `--bare`。

## 目标

- 实现 `RuntimeAdapter` 最小协议：`validate_assets`、`materialize_assets`、`command`、`consume_stream`（最小 LiveEvent）、`discover_trace_files`。
- 实现资产快照装配：隔离 Runtime HOME，按允许清单加载 Skill/Command/Subagent，并用测试确认只加载快照资产。
- 构建评测执行容器镜像：内置 Claude Code、任务依赖与评分前置，digest 可查。
- 实现启动前一致性检查（07 §5）：凭据失效、Ref 未解析、镜像缺失、能力不匹配必须阻塞；容量不足只产生排队警告。

## 验收

- 适配器测试覆盖资产映射与命令生成：结构化变量绑定，生成命令无任意 shell 注入面。
- 执行镜像可重复构建，digest 记录进任务清单。
- 一致性检查按阻塞/警告正确分级，pytest 覆盖全部阻塞路径。
- 一个模拟任务在执行镜像内完成 Claude Code 会话并在 Runner 工作区产出 trace 原始文件（收集即可，解析归 M6）。
