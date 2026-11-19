# [m8-47] Runner 脚本包预置到镜像/OSS，降低 spot 启动依赖

[English](m8-47-runner-scripts-bundle.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-45-queue-progress-lease.*
  - .agents/flow/issues/m8-47-runner-scripts-bundle.*
  - .github/workflows/deploy.yml
  - .github/workflows/eval-shard.yml
  - backend/app/api/runners.py
  - backend/app/config.py
  - backend/app/services/dispatcher.py
  - backend/app/services/provisioning/**
  - backend/app/services/runner_scripts_cache.py
  - docs/architecture.*
  - plans/2026-11-19-pr*-review.md
  - plans/m1-02-fullstack-design/**
  - runner-contract/schemas/v1/dispatch.schema.json
  - runner/**
  - scripts/**
  - tests/**
adr: []
github:
  number: 312
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/312
```

## 背景

#306 现场里 execute job 卡在 `Fetch the scripts bundle` 阶段：脚本包要先用短期 token 找平台元数据端点，再从平台代理下载。spot 网络抖动时，下载重试会把启动拖到十几分钟；PR #308 已经能在 started 前回调 `runner_bootstrap_failed`，但脚本包本身仍依赖平台端点和运行时网络。

内容寻址脚本包、私有 OSS 缓存与确定性 tar 已由 M5-12 提供。本 issue 只改变获取顺序与预置方式，不改变包内容、md5 命名和私有 OSS 语义。

## 目标

- spot 实例在拿到 GitHub job 之前把脚本包预置到 `/opt/eval/runner-scripts/<name>.tar.gz`：镜像可直接烘焙该文件，云初始化为没有烘焙的镜像从私有 OSS 短时预签名 URL 预取并校验 sha256；没有预置时 workflow 从镜像缓存读取。
- dispatch 载荷携带分片冻结的 `scripts_bundle` 名称、md5、sha256 与大小，workflow 不需要先访问平台 scripts 端点也能找到镜像缓存。
- 平台 scripts 元数据增加私有 OSS 预签名 GET URL；workflow 在镜像缓存缺失时优先直连 OSS，其次平台代理，GitHub checkout 只作最后回退。
- 脚本包获取使用短超时（默认 60 秒）并在失败时沿用 #308 的 `runner_bootstrap_failed` 结算，不再等满 queue timeout。
- 实例上不落平台长期凭据；预签名 URL 是短时下载能力，cloud-init 清理时一并移除。
- 发布步骤同时产出确定性镜像缓存目录与 OSS 对象；文档记录发布与回退顺序。

## 验收

- 断开平台 scripts 端点时，spot runner 仍能从镜像缓存取得脚本并执行；镜像缓存缺失时能从私有 OSS 预签名 URL 直连下载。
- 镜像与 OSS 都不可用时，workflow 的脚本包获取在 60 秒内失败，并在 started 前触发 `runner_bootstrap_failed`；平台按现有重试预算生成下一次 attempt。
- 同一源码两次构建的 md5/sha256 一致；预置文件按 `eval-runner-scripts-<md5>.tar.gz` 命名并校验 sha256。
- 实例与 workflow 日志不出现 OSS secret、平台 task token 或长期凭据。
- `npm test`、`pytest tests/backend`、`npm run verify:flow`、`npm run verify:docs` 通过。
