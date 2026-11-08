# 接入固定 Linux 自托管 Runner 与心跳

[English](m2-04-fixed-linux-runner.en.md) | 中文

```yaml flow
kind: issue
milestone: m2
priority: P0
status: done
scope:
  - .agents/flow/issues/m2-04-fixed-linux-runner.*
  - runner/**
  - infra/runner/**
  - scripts/**
  - tests/**
  - backend/**
  - deploy/docker-compose.prod.yml
  - .github/workflows/eval-shard.yml
  - reports/**
adr: []
github:
  number: 14
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/14
```

## 背景

在引入竞价供给前，需要先证明一台固定 Linux 机器可以安全注册、领取平台任务、接受隔离执行并向平台报告存活状态。固定容量把 Runner 接入问题与云实例生命周期问题分开。

## 目标

- 在一台固定 Linux 主机上注册带稳定标签的自托管 Runner。
- 提供 Runner 心跳、在线/离线和忙碌状态的最小持久化与查询。
- 使用只读仓库权限和短期任务凭据，不在 Runner 上保存平台长期密钥。
- 记录 Runner 重启、失联和恢复路径，并限制同一 Runner 同时执行一个 Job。

## 验收

- 平台能显示固定 Runner 的在线状态、标签、最近心跳和当前任务。
- 合成任务可从 GitHub 队列被该 Runner 领取，执行容器内脚本并提交心跳。
- Runner 重启或心跳超时后状态进入离线，恢复注册后重新变为可用。
- Runner 日志与进程环境不包含长期模型 Key、云 AK 或平台管理员令牌。
