# 固定 Linux Runner（M2-04）

在固定 Linux 主机上注册 GitHub Actions self-hosted runner，并运行心跳服务向平台上报在线状态。

## 前置

- 主机已安装 Docker。
- GitHub 仓库已生成一次性 self-hosted runner token（Settings → Actions → Runners → New self-hosted runner → Linux）。
- 平台 API 可访问，且 `.env` 中 `RUNNER_HEARTBEAT_TOKEN` 已配置。

## 安装

```sh
RUNNER_TOKEN='<一次性 token>' \
PLATFORM_BASE_URL='http://47.98.144.243:8080' \
RUNNER_HEARTBEAT_TOKEN='<平台心跳 token>' \
RUNNER_NAME='fixed-linux-eval' \
RUNNER_LABELS='self-hosted,linux,eval' \
bash infra/runner/install.sh
```

脚本会安装 runner 服务、写入 `/etc/eval-platform/runner.env`（权限 600），并启用 `eval-platform-heartbeat.service`。

## 状态与排障

```sh
systemctl status 'actions.runner.*'
systemctl status eval-platform-heartbeat.service
journalctl -u eval-platform-heartbeat.service -n 50 --no-pager
```

平台“资源池”页面显示在线状态、标签、最近心跳和当前任务；心跳超过 90 秒未更新会标记离线。

## 安全边界

Runner 上只保存一次性 GitHub registration token（注册后失效）和平台 scoped heartbeat token；不保存模型 Key、云 AK 或管理员令牌。真实评测的短期凭据/OIDC 在 M2-05/M3 接入。
