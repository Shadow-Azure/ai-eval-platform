# 镜像拉取吞吐优化：串行准备、镜像源不稳与预热缺失

[English](m8-image-pull-throughput.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: backlog
scope:
  - .agents/flow/issues/m8-image-pull-throughput.*
  - backend/app/services/dataset_image_prep.py
  - backend/app/worker/image_prep.py
  - backend/app/services/dataset_image_artifacts.py
  - backend/app/config.py
  - backend/app/api/**
  - deploy/docker-compose.prod.yml
  - deploy/proxy/**
  - tests/backend/**
  - docs/**
  - research/**
  - plans/**
adr: []
github:
  number: 439
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/439
```

## 背景

数据集镜像准备服务把用例镜像预拉取成 OSS tar，供冷启动 Runner 下载；准备链路是单副本、单镜像串行：一个镜像完成 pull → tag → `docker save`/gzip → 上传 → 清理后，才领取下一个。验收时出现一个数据集还差 4 个镜像、另一个数据集的 30 个镜像尚未入队的现象；镜像站单源实测约 2MB/s，镜像约 1–2GB，按此速度仅拉取就要数小时，发起评测被 `409 image_not_ready` 阻塞。本 issue 记录代码与部署环境的定位过程、根因和 P0/P1 方案。

## 定位过程

- 代码链路：`prepare_one` 一次只 claim 一行；`candidate_ref_pairs` 按配置顺序逐源尝试，单源 pull 超时 1800s；成功后 `docker save | gzip -1` 上传内网 OSS，最后 `_cleanup` 删除镜像和临时 tar；worker 单副本。
- 队列与预热：`dataset_image_artifact` 只在 prepare/create 时入队；未发起评测的数据集没有行，因此不会预热。claim 按 `next_attempt_at, id` 排序，同一批入队时间相同，实际是 UUID 顺序；create 要求全部镜像 ready，单个慢镜像阻塞整批。
- 镜像源与失败：部署 `.env` 的 `DATASET_IMAGE_REGISTRIES` 为空，实际使用 DB 覆盖的 8 个源（1ms.run、VVVV、Jiaxin、HubFast、1Panel、Docker Hub、Aliyun ACR、Tencent Mirror）。image-prep 日志显示部分镜像经 VVVV/1ms.run 成功（tar 约 1.2GB），也出现 1ms.run `unexpected EOF`、`Cannot connect to the Docker daemon`、镜像 pull 成功后 `docker tag` 报 `No such image`，以及部分镜像 8 个源全部失败。DinD 没有 `/etc/docker/daemon.json`，没有 daemon 级 registry mirror；宿主 Docker 配置了阿里云 mirror。
- 清理与复用：上传成功后立即删除所有候选镜像和临时 tar；下一镜像即使共享基础层也要重新下载；每个镜像的 tar 包含全部层，OSS 和 Runner 下载都会重复共享层。
- 部署代理实测：现有 `proxy` 是 Squid 正向代理，ACL 只允许 `.github.com` 和 `.githubusercontent.com`，且 `cache deny all`；从部署 ECS 用 `curl -x` 访问 `registry-1.docker.io:443` 返回 `TCP_DENIED/407`，访问 `api.github.com` 返回 200；宿主机直连 Docker Hub 在 20s 内超时。代理与 image-prep 在同一台 ECS，走同一出口，无缓存，因此不能为 Docker 拉取增加带宽。

## 结论与根因

结论：当前慢的主因不是“缺少代理”，而是单 worker 串行 + 上游镜像源慢/不稳 + 预热缺失；代理是 GitHub 专用通道，不能解决 Docker 拉取。

- 根因 1：单副本、单镜像串行，pull、save、upload、cleanup 不重叠，队列吞吐被单镜像限制。
- 根因 2：8 个镜像源没有健康检查/吞吐排序；首个源 1ms.run 出现 `unexpected EOF`，不可用或慢的源会消耗时间；Docker Hub 直连不可达，DinD 又没有 daemon 级 mirror。
- 根因 3：队列只在 prepare/create 时填充；未发起评测的数据集不会预热，且全部镜像 ready 才允许启动，慢镜像直接阻塞验收。
- 根因 4：上传后删除全部本地镜像/层，共享基础层重复下载；tar 按镜像全量打包，OSS 与 Runner 侧也重复共享层。
- 根因 5：GitHub-only Squid 与 image-prep 同机同出口且不缓存；放开域名也不会增加国际带宽，反而多一跳并扩大代理安全边界。

## P0 方案

1. 预热：用现有 prepare/ensure 流程先把待验收数据集的缺失镜像全部入队（尤其尚未发起评测的 30 个），再启动评测；补一个“按数据集版本预热”的入口，避免每次靠 prepare 触发。
2. 选源：对 8 个源做小镜像/manifest 探测，记录 TTFB、成功率、吞吐，按实测排序；短超时跳过不可用源；把 Docker Hub 放到最后，或给 DinD 配置 daemon 级 registry mirror；删除长期不可用的源。
3. 并发与观测：把 DinD 的 `max-concurrent-downloads` 从 3 调到 8；记录每个镜像的 pull 秒数/字节、save 秒数、upload 秒数、tar 大小和 source，先确认瓶颈在拉取还是保存/上传。
4. 代理结论：不要把 GitHub-only Squid 用于 Docker 拉取；它当前直接拒绝 registry，且同机同出口无缓存。若要评估专用代理，需单独部署在链路更好的主机并只放行 registry 域名，先做吞吐测试。

## P1 方案

1. 镜像级并发：2–4 路并发准备（线程池或多 worker），每个任务独立 DB session/workdir；清理按 owner 隔离，启动 sweep 不能删除其他 worker 的临时 tar 或重置其 preparing 行；按磁盘余量做准入。
2. 直传 tar：用 `skopeo`/`crane` 从 registry 直接写 docker-archive，`--image-parallel-copies` 并行层、`--retry-times` 重试，减少 pull+save 双写；验证 `docker load` 兼容。
3. 层复用与去重：保留有界本地镜像/层缓存或本地 registry；按 manifest digest 去重，同 digest 不同 tag 不重复拉/传；评估共享基础层的复用率。
4. 队列优先级：按数据集/评测/用例顺序入队，不再按 UUID；暴露队列位置和 ETA；在数据集导入/版本创建时预取。
5. Registry/代理：评估 Harbor proxy cache 或私有 registry 作为 VPC 分发层；若必须用代理，部署独立 registry 代理并测量吞吐/成本，不复用 GitHub-only Squid。

## 验收

- 预热：待验收数据集的镜像在发起评测前全部入队，readiness 可见，不再因未入队返回 409。
- 选源：健康探测能跳过不可用源，按实测选择最快源；每个源的失败/耗时被记录。
- 吞吐：3 个代表性镜像的拉取/准备时间有可量化改善（目标在基准测试后确定），并发准备不出现重复拉取或清理互相覆盖。
- 代理：文档明确 GitHub-only Squid 不能用于 Docker；若落地专用代理/registry，记录吞吐、成本和放行范围。
- 回归：后端/部署定向测试、`npm test`、`npm run verify:docs`、`npm run verify:flow` 通过；runtime 行为变更只影响镜像准备链路。

## 讨论点

代理实测的结论是“不能加快”：当前 Squid 只放行 GitHub，Docker registry 的 CONNECT 被拒绝；它和 image-prep 同机同出口，且 `cache deny all`，即使放开域名也不会增加带宽。要提速，优先换更快的 registry 镜像/ACR/GA，或把代理/registry 放到国际链路更好的主机；是否为 Docker 单独做代理或私有 registry，需要确认成本、运维和安全边界。
