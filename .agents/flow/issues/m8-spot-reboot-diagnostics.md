# 冷 spot Runner 重启诊断补强

[English](m8-spot-reboot-diagnostics.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - runner/lib/diagnostics.mjs
  - backend/app/services/provisioning/spot_cloud_init.py
  - tests/sigterm-diagnostics.test.mjs
  - tests/backend/test_spot_cloud_init.py
  - .agents/flow/issues/m8-spot-reboot-diagnostics.md
  - .agents/flow/issues/m8-spot-reboot-diagnostics.en.md
  - .agents/flow/issues/m8-spot-reboot-diagnostics.i18n.yaml
adr: []
github:
  number: 345
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/345
```

## 背景

2026-11-19 生产竞争态 spot 的 6 用例运行中，4 个 Runner 在 Agent 执行阶段收到 SIGTERM，平台按 `runner_lost` 结算。现有 `diagnostics/sigterm.json` 只抓 `journalctl -u github-runner`、auth/syslog 与 cloud-init 尾部，无法回答“谁触发了系统级或用户态重启”；同时阿里云 spot 终止时间 metadata 路径写成了 `spot/termination-time`，正确的 `instance/spot/termination-time` 未被请求，返回的 404 不能证明没有 spot 中断通知。

## 目标

- 诊断补齐全量 system journal（含停止前）、`journalctl --list-boots`、上一 boot、apt 与 unattended-upgrades 日志、audit 日志、reboot-required、timers 与内核日志。
- 修正阿里云 spot 终止时间 metadata 路径。
- spot cloud-init 安装并启用 auditd，记录 `systemctl`、`reboot`、`shutdown` 的 execve 与 reboot-required 写入；启用持久 journal。
- 修正 `ExecStopPost=/sbin/shutdown` 在实际镜像上 spawn 失败的问题。

## 验收

- `buildSigtermDiagnostics` 输出新增字段，metadata 请求命中 `instance/spot/termination-time`；测试覆盖正常与降级路径。
- spot cloud-init 生成的 bootstrap 包含 auditd 规则、持久 journal 与可用的关机命令；现有 UserData 16 KiB 上限不破。
- `npm test`、`pytest tests/backend`、`npm run verify:flow` 通过；部署后新建冷 spot 实例生效，下一次 SIGTERM 的 `diagnostics/sigterm.json` 能给出重启发起者或明确无调用者。
