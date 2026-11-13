# [m8-16] 修复代理容器未监听并在部署时输出代理诊断

[English](m8-16-proxy-listen-fix.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-16-proxy-listen-fix.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/**
  - tests/deploy/**
  - docs/**
  - plans/**
adr: []
github:
  number: 213
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/213
```

## 背景

- #212 部署成功，代理容器 `ai-eval-platform-proxy-1` 显示 Started，但 3129 从容器内和外部都 `ECONNREFUSED`：squid 没有监听（很可能镜像默认命令让 squid 守护化，父进程退出后容器结束），评测 install 因此失败。
- 部署健康检查只看 API `/readyz`，代理不在检查范围内，失败时 CI 日志里只有一行 Started，缺少诊断。

## 目标

- compose 强制 squid 前台运行：`command: ["-N", "-f", "/etc/squid/squid.conf"]`。
- 部署在滚动更新后输出 proxy 的 `ps`、`logs --tail=120`、3128/3129 端口探测以及 3129 无 token 探测（预期 407），让 CI 日志可直接定位。
- 不改变 3128 私网 / 3129 公网认证的语义。

## 验收

- 部署日志能看到 proxy 的状态、日志和端口检查；3129 处于监听状态；无 token 返回 407。
- 生产评测 install 经代理成功，design→propose→apply 可执行。
