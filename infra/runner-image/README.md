# 冷 spot Runner 主机镜像

冷 spot 实例从预烘焙的阿里云自定义镜像启动：docker、Node 22、auditd、curl/git/jq/xz 已在镜像内，cloud-init 不再在开机时 `apt-get update/install`。运行期自动更新定时器被 mask，`needrestart` 只列服务不重启，`/var/run/reboot-required` 存在时拒绝接单；job 运行期间由 `systemd-inhibit --what=shutdown:sleep` 阻止关机/休眠。这样安全补丁在镜像构建期完成，评测窗口内不会因 unattended-upgrades 重启 Runner 而收到 SIGTERM。

## 构建周期与版本

- 常规重建：每周一次，从最新官方 Ubuntu 22.04/24.04 镜像创建一台临时按量实例，执行本目录脚本。
- CVE 响应：Ubuntu Security Notice 或阿里云安全公告涉及镜像内包时，24 小时内重建；高危可走紧急重建。
- 版本记录：每次构建写入 `versions/<version>.json`，包含 image_id、镜像名、区域、builder 实例、构建时间和源码 commit；记录提交到仓库，便于审计与回滚。
- 池切换：在资源池 `instance_config.image_id` 填新 image_id；先加一台 `min_capacity=1` 的 warm 实例验证，再让新冷启动实例使用新镜像。
- 回滚：把 `instance_config.image_id` 改回上一条 `versions/*.json` 的 image_id；旧镜像保留至少 30 天，不删除仍在回滚窗口内的镜像。

## 构建步骤

1. 在阿里云控制台用 Ubuntu 22.04/24.04 官方镜像创建一台按量付费 builder（建议 `ecs.g7.large`，40GiB 系统盘，公网带宽足够拉取 Node）。
2. 确认 builder 已安装 Cloud Assistant Agent（官方镜像默认包含）。
3. 在本机安装并配置 `aliyun` CLI 与 `jq`，导出 `ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`。
4. 先 dry-run 检查命令：
   ```sh
   ./infra/runner-image/build.sh --instance-id i-xxx --region cn-hangzhou \
     --version 2026-11-21 --dry-run
   ```
5. 真正构建（会停止 builder 并创建自定义镜像；费用为该实例运行时长与镜像存储）：
   ```sh
   ./infra/runner-image/build.sh --instance-id i-xxx --region cn-hangzhou \
     --version 2026-11-21
   ```
   脚本通过 Cloud Assistant 执行 `prepare.sh`；安装后需要重启时会自动重启 builder 并重跑一次，最终校验定时器为 masked、`/var/run/reboot-required` 不存在，再停止实例并 `CreateImage`。完成后默认删除 builder，保留构建记录与镜像。
6. 把 `versions/<version>.json` 提交到仓库；在资源池里切换 `image_id` 并按下文验证。

不想用脚本时，也可以在 builder 上直接执行 `sudo bash infra/runner-image/prepare.sh`，重启后确认 `/var/run/reboot-required` 不存在，再从控制台“创建自定义镜像”。

## 新实例验收

在新建冷 spot 实例上执行：

```sh
systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer \
  apt-daily.service apt-daily-upgrade.service \
  unattended-upgrades.service ua-timer.timer
# 每行都应是 masked

apt-config dump | grep -E 'APT::Periodic::(Update-Package-Lists|Unattended-Upgrade)'
# 都应输出 "0"

test -f /etc/needrestart/conf.d/99-eval-no-restart.conf
test -f /etc/eval/runner-image-baked
test ! -f /var/run/reboot-required

systemctl show github-runner -p ExecStart
# 应包含 systemd-inhibit --what=shutdown:sleep --mode=block
```

`eval-shard` 的 `runner-smoke` 作业会自动执行同一组检查，任一失败即冒烟失败。SIGTERM 后的 `diagnostics/sigterm.json` 新增 `auto_update_units`、`apt_periodic`、`needrestart` 与 `runner_image` 字段，可直接确认实例是否来自已加固镜像。

## 与 cloud-init 的兼容路径

镜像未切换前，stock Ubuntu 云镜像仍由 cloud-init 安装依赖；它先写 APT/needrestart 配置并 mask 定时器，再用 `--no-upgrade` 安装，避免在启动时顺带升级系统包。镜像切换后 cloud-init 检测到 `/etc/eval/runner-image-baked` 就跳过全部 apt 与 Node 下载。两条路径都在启动前检查 `reboot-required`，并以 `systemd-inhibit` 保护 job。

## 安全取舍

运行期不再自动更新，镜像滞后风险由固定周期重建、CVE 监控和紧急重建通道补偿。`systemd-inhibit` 不是硬隔离，强制关机或硬件事件仍可能绕过；它只作为禁用自动更新之外的兜底，不能替代镜像更新。现有运行中的实例不会变化，改动只对新冷 spot 实例生效。
