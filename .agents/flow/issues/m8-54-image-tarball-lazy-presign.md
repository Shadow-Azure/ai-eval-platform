# [m8-54] 镜像 tarball 预签名改为容量准入后按用例签发

[English](m8-54-image-tarball-lazy-presign.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-54-image-tarball-lazy-presign.*
  - .agents/notes/adr-0003-image-tarball-lazy-presign.*
  - docs/design/eval-platform-architecture.*
  - docs/architecture.*
  - plans/2026-11-22-pr373-review.md
  - runner/scripts/real-task.mjs
  - runner/lib/task-payload.mjs
  - runner/lib/image-tarball.mjs
  - backend/app/config.py
  - backend/app/services/dataset_image_tarballs.py
  - backend/app/api/runners.py
  - tests/runner/**
  - tests/backend/**
adr:
  - ../../notes/adr-0003-image-tarball-lazy-presign.md
github:
  number: 371
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/371
```

## 背景

生产评测 `148b0b6d-86bc-4739-8b48-28d9caadccee`（MiniMax-M3.1-Flash × fixed-linux × OpenSpec-cn，6 个 SWE-bench 用例，Agent 超时 7200s、case 超时 8400s、并发 1）终态 `partial`：2 个用例执行后未解决，4 个用例在 Agent 启动前以 `image_unavailable` 失败。

4 个失败用例的 GitHub Actions job 日志都出现 `image_tarball_fallback`，下载阶段返回 `curl: (22) The requested URL returned error: 403`；随后回退到公开镜像源 `docker.1ms.run`、`proxy.vvvv.ee`、`docker.jiaxin.site`、`free.hubfast.cn`，全部报错或额度耗尽。OSS 对象本身存在（HEAD 实测约 1.12GB/个），不是缓存缺失。

UTC 时间线：6 个 execute job 在 14:41 左右启动并取 `/tasks`；运行并发为 1，前两例运行到 16:40；其余 4 例等到 16:41–16:56 才拿到容量，此时 14:41 签发、TTL 3600s 的预签名 URL 已过期约 1 小时。用例 2 在 15:30 用同一批 URL 下载成功（41s），刚好在 TTL 内。

## 根因

`runner/scripts/real-task.mjs` 在 job 启动时一次性拉取 `/tasks`（L1151），平台随即为每个 case 签发 OSS 预签名 URL；之后才进入容量等待（L1425 的 `started` 回调 429 重试，等待上限为 `case_timeout + 300s`，本例 8700s）。镜像下载发生在拿到容量之后，于是 URL 年龄等于排队时长加启动开销，可能远超 TTL（`dataset_image_tarball_ttl_seconds` 默认 3600s，见 `backend/app/services/dataset_image_tarballs.py`）。

两个放大因素：

- 分片内所有 case job 会同时落到空闲 runner 上（fixed 与 spot 共用 `self-hosted,linux,eval` 标签），在平台配额上排队而不是在 GitHub 队列等待；runner 被占住，URL 也一起老化。
- 公开镜像源不可靠，作为回退会把“凭证过期”放大成“镜像不可用”。

## 修复方案

把每个用例的 `/tasks`（或专用 image-tarball 端点）拉取移动到 `started` 回调成功之后、拉镜像之前：

1. `runner/scripts/real-task.mjs`：manifest 仍可提前取；`taskByCase` 改为按需获取——在 `postStartedWithCapacityWait` 返回 2xx 后，用同一 runner token 再取一次 `/tasks`（或调用按 case 的 URL 端点），随即下载镜像。
2. TTL：预签名只需覆盖下载（15–30 分钟），不再覆盖排队；`backend/app/services/dataset_image_tarballs.py` 按下载预算加余量计算，避免与调度参数耦合。
3. 403 兜底：首次 403 时刷新一次 `/tasks` 并重试一次；仍失败才回退 `image_sources`，并区分“鉴权失败”与“对象缺失”。
4. 测试：runner 单测覆盖“容量获取前不取 `/tasks`、不签发 URL”“429 等待后取到新 URL”“403 刷新仅重试一次”；后端单测覆盖 TTL 计算。

非目标（另行拆分）：把容量排队前移到调度层（`max_parallel` / 平台队列）、fixed/spot 标签隔离、内网 Registry + 短时 token 的长期方案。

## 验收

- 复现条件（6 用例、并发 1、单例 50 分钟以上）下，4 个排队用例拉镜像不再出现 403，`image_unavailable` 为 0。
- 单测断言：`/tasks` 请求发生在 `started` 2xx 之后；URL TTL 只按下载预算计算；403 刷新仅重试一次。
- `npm test`、`npm run verify:flow`、`npm run verify:docs` 通过。
- 文档：设计文档与 ADR-0003 描述“容量准入后按用例签发、TTL 仅覆盖下载”的契约。

## 关联

- ADR：`.agents/notes/adr-0003-image-tarball-lazy-presign.md`
- 设计文档：`docs/design/eval-platform-architecture.md` 第 4、7 节
