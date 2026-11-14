# [m8-23] 部署后自动清理旧镜像，避免系统盘被占满

[English](m8-23-deploy-image-retention.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - .agents/flow/issues/m8-23-deploy-image-retention.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - deploy/deploy.sh
  - deploy/common.sh
  - .github/workflows/deploy.yml
  - tests/deploy/**
  - plans/**
adr: []
github:
  number: 228
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/228
```

## 背景

部署机 40G 系统盘在 2026-11-14 诊断时已 100% 占满，Docker 镜像占 22.5G；2026-11-08 以来 71 次部署，`deploy/deploy.sh` 每次按 digest 拉取新的 api/web 镜像，脚本与部署 workflow 都没有清理或保留策略，累计 68 个 api 与 68 个 web 旧版本。业务数据与日志合计不足 1G，手工清理可回收约 18–20G，但只要部署继续，旧镜像仍会重新占满。

## 目标

- 部署成功后自动删除未被运行中容器使用、且超出保留窗口的本地镜像，保留当前运行镜像与最近 N 个可回滚版本（N 有界，建议 2–3；`.state/previous.env` 引用的 digest 必须保留）。
- 清理步骤幂等并输出保留/删除数量与 `df` 水位；失败时给出明确日志，不把已完成的部署判为失败。
- 不在主机上依赖人工登录执行清理。

## 验收

- 连续多次部署后 `docker system df` 的镜像占用保持有界，超出保留窗口的旧版本被删除，回滚目标仍可用。
- `deploy/` 的测试覆盖保留集合边界：运行中镜像、上一版 digest、待删除旧版本。
- 生产验证：部署成功后 `df -h /` 可用空间恢复并保持，清理日志可在 GitHub Actions 输出中看到。
