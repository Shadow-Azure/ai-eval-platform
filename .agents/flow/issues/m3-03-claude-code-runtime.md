# 接入 Claude Code Runtime 与单用例执行包

[English](m3-03-claude-code-runtime.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
  - runner/**
  - runner-contract/**
  - .github/workflows/**
adr: []
github:
  number: 25
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/25
```

## 背景

Runner 已能执行确定性合成任务，但还不能驱动真实 Agent。M3 需要注册 Runtime 适配器，在隔离沙箱内准备代码与资产、执行 Claude Code、收集补丁和会话文件。

## 目标

- 注册 agent_runtime 与 RuntimeAdapter 接口，实现 Claude Code headless 适配与隔离 HOME、资产映射和 stream-json 解析。
- 扩展 in-job runner：领取任务包、准备仓库与环境、执行 Agent、收集补丁与会话文件并上传对象存储。
- 把 Agent 执行与评分分成独立阶段，产物 manifest 与回传 Schema 保持版本化一致。
- 区分环境、配置、Agent 和上传失败，给出可重试或快速失败分类。

## 验收

- 单个 SWE-bench 用例可在 Linux 沙箱内完成 Agent 执行并生成补丁与原生会话文件。
- 资产快照只加载被选择的 Skill、Command 与 Subagent，Agent 无法读取评分专用文件。
- Agent 非零退出、超时和上传失败都会回传可诊断错误，不产生成功终态。
- 本地 fixture 与真实 workflow 使用同一份 manifest 和回调 Schema。
