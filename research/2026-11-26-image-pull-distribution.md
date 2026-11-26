# 镜像拉取与分发优化调研

> 2026-11-26 · 基于当前仓库的 image-prep / runner 链路与公开资料整理

## 0. 结论

- 当前慢的主因不是“没有分发方案”，而是**上游单源 + 单 worker 串行**：image-prep 一次只处理一个镜像，按配置顺序逐个源尝试；如果单源只有约 2MB/s，数十个镜像就是十几到二十几小时。
- 最优先的三件事（不改架构）：
  1. 按实测吞吐重排/替换镜像源，别让 Docker Hub 或慢镜像站占住第一顺位；阿里云 ECS 上评估 ACR 制品订阅 / GA（旧的 ACR 加速器已停止同步最新镜像）。
  2. 对尚未发起评测的数据集，**提前入队预热**，不要等发起评测才入队；队列按批次/优先级推进。
  3. 提高单镜像层下载并发（DinD `max-concurrent-downloads` 3→8），再试 2–4 路镜像级并发；用数据决定。
- 中期再考虑本地 registry / 层缓存 / zstd；P2P（Dragonfly、Kraken）和懒加载（Nydus、eStargz）在当前规模不划算。

## 1. 当前链路（从代码看）

```text
prepare/create → dataset_image_artifact 入队
        ↓
image-prep worker（单副本、单镜像）
  按 DATASET_IMAGE_REGISTRIES 顺序：
    docker pull <mirror>/<repo>:<tag> --platform linux/amd64
        ↓ 失败换下一个源
    docker tag → 规范引用
    docker save | gzip -1 → /work/dataset-image-*.tar.gz
    PUT 内网 OSS → HEAD 校验 → ready
    docker image rm（临时 tar + 所有候选镜像）
        ↓
runner 拿 presign 内网 GET URL → curl .tar.gz → docker load → 校验
（固定 runner 本地已有镜像则跳过下载；冷 spot 必须下载）
```

关键事实：

- 单 worker、单镜像；`prepare_one` 一次只 claim 一行。
- 源顺序 = 配置顺序；每个源失败才换下一个；单源 pull 超时 1800s。
- 拉完即删本地镜像/层，下一镜像即使共享基础层也要重新下载。
- tar 用单线程 gzip level 1；`docker load` 支持 gzip / bzip2 / xz / zstd。
- 队列只在 prepare/create 时入队；没发起评测的数据集不会自动预热。
- claim 顺序是 `next_attempt_at, id`；同一批入队时间相同，实际按 UUID 顺序，不是用例优先级。
- 所有镜像 ready 才允许启动；一个慢镜像阻塞整批。

## 2. 时间花在哪

| 阶段 | 现状 | 可能的瓶颈 |
|---|---|---|
| 上游 pull | 单源约 2MB/s（1Panel），失败才切换 | 镜像站限速/跨境链路；单连接；Docker 层并发默认 3 |
| save | `docker save` 全量读盘 | 大镜像 I/O + 单线程 gzip CPU |
| upload | 内网 OSS | 通常不是瓶颈；跨 region/公网会慢 |
| runner load | 下载 tar + `docker load` | 冷 spot 下载预算 900s；gzip 解压 CPU |

按 2MB/s 粗算：1GB≈8.5 分钟，5GB≈42 分钟；30–40 个镜像 × 平均 3–5GB ≈ 90–200GB，仅 pull 就要约 13–28 小时。这就是“好几个小时”的量级来源；需要实测确认镜像大小和每阶段耗时。

## 3. 业界方案对比

| 方案 | 解决什么 | 对当前问题 | 成本/风险 |
|---|---|---|---|
| 镜像加速器 / 镜像站 | 跨境、单源慢 | 立竿见影，但第三方限速/不稳定 | 低；需实测、固定 digest |
| 私有 registry / Harbor proxy cache | 重复拉取、限流、层去重 | 首次仍走上游；对数十个新镜像帮助有限 | 中；运维/存储/GC |
| 多源并发 / 多 worker | 单连接/单源限速 | 若镜像站按连接限速，2–4 路可聚合带宽 | 中；磁盘/清理隔离 |
| skopeo/crane 直接 registry→tar | 去掉 pull+save 双写，层并发/重试 | 降低单镜像准备时间，但不改上游带宽 | 低–中；需验证 tar 兼容 |
| zstd / pigz | gzip CPU、体积 | 降低 save/load 时间；Docker load 支持 zstd | 低；key/格式迁移 |
| 本地层缓存 / registry | 共享基础层重复下载 | 30 个 SWE-bench 镜像可能共享大量层 | 中；磁盘 |
| Dragonfly / Kraken P2P | 大规模多节点分发 | 当前 runner 少，收益有限 | 高；组件多 |
| Nydus / eStargz 懒加载 | 启动前不拉全量 | 需 containerd/snapshotter；当前 Docker 链路改动大 | 高；运行时改造 |
| 预热 / 优先级队列 | 不阻塞发起，先拉要用的 | 直接解决“没发起就未入队” | 低–中；产品/接口 |

补充说明：

- Docker Hub 对未认证用户是 100 次/6 小时、Personal 200 次/6 小时，付费账号不限量；镜像站/缓存也受对上游的限流影响（[Docker Hub 限额](https://docs.docker.com/docker-hub/usage/pulls/)）。
- Docker 守护进程默认 `max-concurrent-downloads=3`，即单镜像最多 3 层并行下载；可调大（[dockerd 手册](https://manpages.debian.org/bookworm/docker.io/dockerd.8.en.html)）。
- `docker load` 已支持 zstd 压缩的 tar，切 zstd 不需要改 runner 的 load 命令（[docker load 文档](https://docs.docker.com/reference/cli/docker/image/load/)）。
- Harbor 的 proxy cache 可以把公开 registry 的镜像缓存在本地，减少重复拉取和对上游的请求/带宽消耗（[Harbor Proxy Cache](https://goharbor.io/docs/edge/administration/configure-proxy-cache/)）。
- Dragonfly 用 P2P 做镜像/文件分发，提供 preheat（预热）接口，可指定并发层数和 peer 数（[Dragonfly](https://d7y.io/docs/)、[Preheat](https://d7y.io/docs/advanced-guides/open-api/preheat/)）。
- Kraken 是 Uber 的 P2P registry，面向大规模集群；官方称其生产环境每天分发 100 万+ blob（[Kraken](https://uber-kraken.readthedocs.io/en/latest/)）。
- eStargz 的 lazy pulling 让容器不必等全量拉取完成；项目引用 FAST '16 的数据称拉取可占容器启动时间的 76%（[stargz-snapshotter](https://github.com/containerd/stargz-snapshotter)）。
- Nydus 支持按 chunk 按需加载、跨层/跨镜像去重，存储后端支持 Registry、OSS、NAS 和 P2P；Docker 侧只有实验性的 graphdriver（[Nydus](https://github.com/dragonflyoss/nydus)）。
- skopeo 支持 `--image-parallel-copies` 并行复制层、`--retry-times` 重试，可直接输出 docker-archive（[skopeo-copy](https://manpages.debian.org/unstable/skopeo/skopeo-copy.1.en.html)）。
- 阿里云 ACR 的官方镜像加速器已停止同步最新镜像，且仅阿里云 ECS 可访问（非 ECS 返回 403）；官方建议改用 ACR 制品订阅或全球加速 GA（[ACR 官方镜像加速](https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images)、[制品订阅](https://help.aliyun.com/zh/ack/ack-managed-and-ack-dedicated/user-guide/subscribe-to-image-tags)）。

## 4. 建议（按投入排序）

### P0：今天可做（不改代码/只改配置）

1. **重排镜像源并实测**：在同一台 image-prep 主机上，用 3 个代表性镜像分别测 Docker Hub、1Panel、DaoCloud 等；把最快且稳定的放第一。阿里云 ECS 上评估 ACR 制品订阅/GA；注意 ACR 加速器已停止同步最新镜像，且仅阿里云 ECS 可用。
2. **提前预热**：用现有 prepare/ensure 流程先把尚缺镜像的数据集冻结并入队（哪怕暂不启动评测），按批次/优先级推进；后续再加“一键预热数据集镜像”的入口。
3. **提高层并发**：DinD 启动参数加 `--max-concurrent-downloads=8`（默认 3），重启后测 3 个镜像；若单镜像层数少，收益有限。
4. **加计时**：记录每个镜像的 pull 秒数/字节、save 秒数、upload 秒数、tar 大小；先确认 2MB/s 是单源限速还是主机出口上限。

### P1：小改动（1–3 天）

5. **2–4 路镜像级并发**：单进程线程池（每个线程独立 DB session + 独立 workdir）或多 worker；需要 owner 级清理、启动 sweep 只清自己的、磁盘余量准入。当前单副本 + 全局 sweep 是并发的主要障碍。
6. **skopeo/crane 替代 pull+save**：`skopeo copy --image-parallel-copies N --retry-times 3 docker://<mirror>/... docker-archive:/work/x.tar`，减少一次落盘和 Docker daemon 依赖；验证 `docker load` 兼容。
7. **层复用**：保留有界本地镜像/层缓存，或本地 registry；按 manifest digest 去重（同 digest 不同 tag 不重复拉/传）。
8. **压缩**：zstd（`docker load` 支持）或 `pigz`；预计降低 save/load 的 CPU 时间；需要新 key/格式迁移。
9. **队列优先级**：按数据集/评测/用例顺序入队，不要按 UUID；给不同数据集/批次明确优先级；暴露队列位置/ETA。

### P2：中期（按规模再评估）

10. **私有 registry**：Harbor proxy cache 或 zot，镜像 prep 只做 upstream→registry，runner 直接 pull；层去重、并行、无 tar；需要 registry 运维/GC/鉴权。
11. **P2P/懒加载**：Dragonfly/Kraken 适合大规模多节点；Nydus/eStargz 需要 containerd；当前数十个镜像/少量 runner 不建议先上。

## 5. 验证计划

- 选 3 个镜像（小/中/大），在同一主机、同一时段测：
  - 源：Docker Hub / 1Panel / DaoCloud / ACR（如可用），各 3 次；
  - 并发：1 / 2 / 4；
  - 压缩：gzip -1 / zstd -3；
  - 记录：TTFB、MB/s、失败率、pull 秒、save 秒、upload 秒、tar 大小。
- 目标：找出“单源最快 + 并发是否聚合 + save/upload 占比”，再决定是否改代码。
- 建议先做 2 路并发 + 最快源 + 预热，通常能把总时长降到原来的 1/2–1/3（需实测）。

## 6. 风险 / 不推荐

- 不要只调大超时：会把“慢”变成“更久地等”。
- 第三方镜像站：可用性和信任风险；固定 digest，不要用 `latest` 漂移。
- 并发前先做清理隔离：当前启动 sweep 会清掉其他 worker 的临时 tar/把 preparing 行重置；直接 `--scale` 会重复拉取/覆盖。
- 私有 registry、P2P、懒加载都需要额外运维或运行时改造，不应在“先把当前批次跑完”之前上。

## 7. 参考

1. Docker Hub pull usage and limits — https://docs.docker.com/docker-hub/usage/pulls/
2. dockerd(8) man page（`max-concurrent-downloads` 默认 3）— https://manpages.debian.org/bookworm/docker.io/dockerd.8.en.html
3. docker image load（支持 gzip/bzip2/xz/zstd）— https://docs.docker.com/reference/cli/docker/image/load/
4. Harbor Proxy Cache — https://goharbor.io/docs/edge/administration/configure-proxy-cache/
5. Dragonfly 介绍 — https://d7y.io/docs/
6. Dragonfly Preheat — https://d7y.io/docs/advanced-guides/open-api/preheat/
7. Kraken — https://uber-kraken.readthedocs.io/en/latest/
8. stargz-snapshotter（eStargz lazy pulling）— https://github.com/containerd/stargz-snapshotter
9. Nydus — https://github.com/dragonflyoss/nydus
10. skopeo-copy(1) — https://manpages.debian.org/unstable/skopeo/skopeo-copy.1.en.html
11. 阿里云 ACR 官方镜像加速 — https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images
12. 阿里云制品订阅获取海外源镜像 — https://help.aliyun.com/zh/ack/ack-managed-and-ack-dedicated/user-guide/subscribe-to-image-tags
