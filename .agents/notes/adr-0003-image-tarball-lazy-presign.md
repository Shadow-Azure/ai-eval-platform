# ADR 0003 · 镜像 tarball 预签名的签发时机与 TTL 边界

[English](adr-0003-image-tarball-lazy-presign.en.md) | 中文

- 状态：已采纳
- 日期：2026-11-22
- 适用 issue：m8-54（镜像 tarball 预签名改为容量准入后按用例签发）
- 设计依据：`docs/design/eval-platform-architecture.md` 第 4、7 节；生产评测 `148b0b6d-86bc-4739-8b48-28d9caadccee` 的失败证据

## 背景

Runner 在 execute job 启动时拉取 `/tasks`，平台随即为每个用例签发 OSS 镜像 tarball 的短时预签名 URL；之后 Runner 才在 `started` 回调上等待平台并发配额。生产评测中，4 个用例在配额队列里等待超过 2 小时，轮到下载时 3600s 的 URL 已过期，下载返回 403，回退的公开镜像源又全部不可用，最终以 `image_unavailable` 失败。OSS 对象本身完好，失败与缓存是否存在无关。

如果只把 TTL 拉长到“分片用例数 × 用例超时”，URL 仍会随重试、暂停恢复、并发排队和 runner 抢占而再次过期；同时 1.1GB 级镜像的不可撤销 bearer URL 暴露面会随时长放大。签发时机与调度等待耦合才是根因。

## 决策

1. 镜像 tarball 的预签名 URL 必须在容量准入成功后、按用例签发。Runner 在 `started` 回调返回 2xx 之后才拉取 `/tasks`（或调用按 case 的 image-tarball 端点），随即下载镜像。
2. 预签名 TTL 只覆盖下载窗口，默认 15–30 分钟，按下载预算加余量计算；不覆盖排队等待。
3. 首次下载返回 403 时，允许刷新一次 `/tasks` 后重试一次；仍失败才回退 `image_sources`，并在错误分类中区分鉴权失败与对象缺失。
4. 容量等待上限保持 `case_timeout + 300s`；本决策不调整排队位置与并发模型。
5. 不使用“把 TTL 拉长到 N × case_timeout 或 24 小时”作为方案；它只是把时间竞态推远，并放大不可撤销凭证的暴露面。

## 备选

- 长 TTL：拒绝。不可撤销、与调度参数耦合、仍有超时可能。
- 平台代理下载或内网 Registry + 短时 token：更彻底的方向，另行立 issue。
- 调度层容量准入（`max_parallel` / 平台队列）：与本案互补，另行立 issue。

## 影响

- Runner 的 `/tasks` 调用时机改变；`/tasks` 响应结构不变，平台 API 契约不变。
- 设计文档第 4、7 节描述“容量准入后按用例签发、TTL 仅覆盖下载”的契约；`docs/architecture.md` 在当前行为落地后随代码 PR 更新。
- 测试需覆盖容量获取前不取 `/tasks`、429 等待后取到新 URL、403 刷新仅一次，以及 TTL 计算。
- 回滚：保留 TTL 下限作为兜底；若需回退到 job 启动时取 `/tasks`，必须同时给出不小于容量等待上限的 TTL，不推荐。
