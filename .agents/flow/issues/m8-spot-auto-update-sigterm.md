# 冷 spot 自动安全更新在评测中触发 Runner SIGTERM

[English](m8-spot-auto-update-sigterm.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - backend/app/services/provisioning/spot_cloud_init.py
  - tests/backend/test_spot_cloud_init.py
  - runner/lib/diagnostics.mjs
  - tests/sigterm-diagnostics.test.mjs
  - docs/architecture.md
  - docs/architecture.en.md
  - .agents/flow/issues/m8-spot-auto-update-sigterm.md
  - .agents/flow/issues/m8-spot-auto-update-sigterm.en.md
  - .agents/flow/issues/m8-spot-auto-update-sigterm.i18n.yaml
adr: []
github:
  number: 348
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/348
```

## 背景

2026-11-20 的评测 8f43561f 在竞争态 spot 池运行，astropy__astropy-12907 在 Agent 执行约 13 分钟后收到 SIGTERM（runner_lost）。#346 已部署 auditd、持久 journal 与 apt/unattended-upgrades 诊断，本次捕获到完整证据：冷 spot 实例默认启用 apt-daily/unattended-upgrades，随机 timer 落在 60 分钟评测窗口内，自动升级系统包后触发服务重启，runner 被 systemd 停掉。

## 分析过程

- 运行现场：12907 attempt 1，job 44662253385，实例 i-bp10mlmax6tmr2da04eb，02:36:36 创建（#346 部署后），02:52:34 SIGTERM。
- 自动更新：10:51:37 apt-daily-upgrade.timer 触发；10:51:49–10:52:31 unattended-upgrade 升级 libc6、libc-bin、locales、systemd、perl、python3-twisted 等。
- reboot-required：/var/run/reboot-required 内容为 “*** System restart required ***”。
- 重启现场：10:52:34 systemd 进入 final-sigterm，networkd、sshd、containerd、auditd 等服务整体重新拉起；journalctl --list-boots 仍只有一个 boot（10:36:54–10:52:35），说明是用户态/服务级重启而非整机重启。
- 平台侧：实例 provider_state=Running、无 OperationLocks、spot termination-time 返回 404（无 spot 中断通知）；不是云侧回收。auditd 最后 60 行只有诊断自身的 systemctl 调用，没有显式 reboot 命令。
- 根因：unattended-upgrades 的包升级触发了服务重启（needrestart/systemd 路径），systemd 停掉 github-runner，job 收到 SIGTERM。

## 解决方案

- 立即修复：cloud-init 禁用运行期自动更新——写入 APT::Periodic::Update-Package-Lists "0" 等配置，systemctl mask apt-daily.timer、apt-daily-upgrade.timer、unattended-upgrades.service、ua-timer.timer，并在 /etc/needrestart/conf.d 关闭自动服务重启。
- 依赖固化：把 docker、node、auditd 固化进 runner 镜像，cloud-init 不再 apt-get update/install，避免 boot 时顺带升级包。
- 长期方案：按不可变基础设施做定期镜像重建（如每周从最新 Ubuntu 基础镜像构建、测试、更新池 image_id/digest），安全补丁在构建期完成，实例始终从已打补丁镜像启动。
- 兜底：execute job 外层加 systemd-inhibit --what=shutdown:sleep --mode=block；若 boot 时已存在 /var/run/reboot-required，runner 拒绝接单而不是在 job 中重启。
- 安全取舍：禁用运行期自动更新后必须建立镜像更新 SLA、CVE 监控和紧急重建通道，不能只禁更新而不补镜像。

## 验收

- 新建冷 spot 实例上 apt-daily.timer、apt-daily-upgrade.timer 为 masked，APT Periodic 项为 0，needrestart 不自动重启服务。
- 60 分钟评测窗口内不再出现 unattended-upgrade 触发的服务重启或 SIGTERM；diagnostics/sigterm.json 的 apt/unattended 字段为空或仅含 boot 时安装。
- cloud-init 生成的 UserData 仍在 16 KiB 上限内，相关单元测试覆盖；部署到生产后新建实例生效。
- 镜像重建流程有固定周期和版本记录，池切换后可回滚。

## 风险

- 不运行期更新的代价是镜像可能滞后；必须用固定周期重建和 CVE 响应补偿。
- systemd-inhibit 不是硬隔离，强杀或硬件事件仍可能绕过；不能替代禁用 timer 和镜像更新。
- 现有正在运行的实例不会自动变化；改动只对新冷 spot 实例生效。

## 参考

- Ubuntu automatic updates：https://ubuntu.com/server/docs/how-to/software/automatic-updates
- AWS OPS03-BP05 immutable patching：https://docs.aws.amazon.com/wellarchitected/latest/framework/ops03-bp05.html
- Ubuntu needrestart services restart：https://lists.ubuntu.com/archives/ubuntu-devel-discuss/2024-June/019711.html
- systemd-inhibit：https://www.freedesktop.org/software/systemd/man/256/systemd-inhibit.html
