# runner 镜像下载与 checkout 异步化：避免长任务阻塞事件循环

[English](m8-runner-async-io.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - runner/**
  - tests/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 363
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/363
```

## 背景

- #362 定位到生产评测 `model_token_unavailable` 的直接原因：per-case manifest 刷新复用了已被平台关闭的 keep-alive 连接；runner 在刷新前用 `spawnSync` 下载/加载镜像 tar（冷机约 42 秒）和 `git checkout`（约 5 秒），阻塞了 Node 事件循环，undici 收不到服务端的 FIN。
- #362 的 P0 用一次性、非池化连接消除了这个具体失败；本 issue 跟进根因加固：不要让长任务阻塞 runner 的事件循环，避免同类竞态和取消/诊断延迟。
- 同步调用期间主进程无法处理网络 I/O、取消与回调；心跳由独立 sidecar 承担，但主进程发出的控制面请求仍会被拖延。

## 目标

- 新增 `runner/lib/run-command.mjs`：基于 `child_process.spawn` 的 Promise 封装，支持 `timeout`、`maxBuffer`、`AbortSignal`、stdout/stderr 采集、非零退出码与错误码映射。
- `loadImageTarball` 改为 async，下载与 `docker load` 使用异步命令；失败仍返回 `{ok:false, stage, error}`，并保留 `trace/image-tarball.json`。
- `real-task.mjs` 的 `git checkout` 与递归 `chmod` 改为 `await` 异步命令，失败仍走原来的错误路径。
- 事件循环在镜像下载/加载期间保持可响应，同时保留现有超时、输出采集、诊断文件与退出码语义。

## 非目标

- 不修改 token 刷新协议、manifest 端点或网关授权。
- 不改 workflow / dispatch 结构。
- 不要求把所有 `spawnSync` 都改掉：短、快、必须在启动顺序中同步完成的调用可以保留，但需要逐个评估。

## 验收

- 冷镜像场景下，镜像下载期间事件循环不被阻塞（定时器漂移或日志时间戳验证）。
- 镜像下载/加载与 checkout 的失败诊断、超时与错误码与现状一致。
- `node --test tests/runner/*.test.mjs` 全绿。
- 结合 #362 的 P0，生产复跑冷镜像评测不再出现 `model_token_unavailable`。
