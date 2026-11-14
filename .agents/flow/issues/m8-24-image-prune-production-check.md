# [m8-24] 验证部署镜像清理的真实删除路径

[English](m8-24-image-prune-production-check.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - .agents/flow/issues/m8-24-image-prune-production-check.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - deploy/common.sh
  - deploy/deploy.sh
  - tests/deploy/**
  - plans/**
adr: []
github:
  number: 231
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/231
```

## 背景

m8-23 的部署后镜像清理已随 #230 上线；2026-11-14 的生产 Deploy #43546086523 已执行清理，但当时每个平台仓库恰好只有 3 个版本，日志为 `retention=3 candidates=8 kept=8 removed=0 failed=0`，真实 daemon 上的删除路径没有被触发。假 docker 测试覆盖了删除边界，但 `docker image rm <id>` 对按 digest 拉取、tag 为 `<none>` 的镜像是否成功，仍需等下一次出现第 4 个版本时确认。

## 目标

- 在后续一次产生第 4 个镜像版本的部署中，确认清理日志出现 `removed>0` 且 `failed=0`，运行中镜像、`.state/previous.env` 回滚 digest、每仓库最近 3 版均保留。
- 若真实删除失败（例如 digest 引用需要 `-f` 或改用 `repo@digest`），修复删除方式并补测试。
- 该验证不新增云资源。

## 验收

- 生产 Deploy 日志出现 `deploy: image prune: ... removed>0 ... failed=0`，`df -h /` 可用空间不因镜像累积而下降。
- 删除后 api/web/worker/proxy 健康，回滚所需 digest 仍在。
- 若发生修复，`tests/deploy/image-retention.test.mjs` 覆盖对应真实引用形式，CI 通过。
