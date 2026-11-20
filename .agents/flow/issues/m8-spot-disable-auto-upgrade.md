# 冷 spot 禁用运行期自动安全更新并固化 Runner 主机镜像

[English](m8-spot-disable-auto-upgrade.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/app/services/provisioning/spot_cloud_init.py
  - runner/lib/diagnostics.mjs
  - tests/backend/test_spot_cloud_init.py
  - tests/backend/test_aliyun_provisioner.py
  - tests/sigterm-diagnostics.test.mjs
  - tests/integration/m4_04_spot_smoke.py
  - tests/runner-image.test.mjs
  - plans/2026-11-20-pr349-review.md
  - infra/runner-image/**
  - .github/workflows/eval-shard.yml
  - docs/architecture.md
  - docs/architecture.en.md
  - docs/architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - .agents/flow/issues/m8-spot-disable-auto-upgrade.md
  - .agents/flow/issues/m8-spot-disable-auto-upgrade.en.md
  - .agents/flow/issues/m8-spot-disable-auto-upgrade.i18n.yaml
adr: []
github:
  number: 348
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/348
```

## 背景

2026-11-20 的评测 8f43561f 在竞争态 spot 池运行，astropy__astropy-12907 在 Agent 执行约 13 分钟后收到 SIGTERM。#346 部署的诊断显示，冷 spot 实例默认启用的 apt-daily/unattended-upgrades 随机 timer 落在 60 分钟评测窗口内，自动升级 libc/systemd 等系统包后通过 needrestart 触发服务重启，systemd 停掉了 github-runner。云侧没有 spot 中断，根因是运行期自动更新。

## 目标

- cloud-init 在任何包操作前写入 APT Periodic 0 配置、mask apt-daily/unattended-upgrades 单元，并关闭 needrestart 自动重启。
- 用预烘焙主机镜像固化 docker、Node、auditd 等依赖；镜像内的实例跳过运行期 apt 与 Node 下载，stock 云镜像保留一次 `--no-upgrade` 兼容安装。
- 提供固定周期的镜像重建、版本记录、池切换与回滚流程，安全补丁在构建期完成。
- 启动时若已存在 `/var/run/reboot-required` 则拒绝接单；job 生命周期内用 `systemd-inhibit --what=shutdown:sleep` 阻止关机/休眠。
- SIGTERM 诊断与 runner-smoke 能直接确认实例的自动更新状态和镜像来源。

## 验收

- 新建冷 spot 实例上六个自动更新单元均为 masked，APT Periodic 项为 0，needrestart 不自动重启；UserData 仍在 16 KiB 内。
- 存在 reboot-required 时不启动 Runner；运行中的 Runner 单元带 shutdown/sleep inhibitor。
- 镜像构建脚本可 dry-run 并生成带 image_id 的版本记录；文档给出每周重建、CVE 响应、池切换和回滚步骤。
- `npm test`、`pytest tests/backend`、文档与 flow 门禁通过；runner-smoke 在真实冷 spot 实例上验证加固项。
