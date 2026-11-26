# 镜像预处理走内网 OSS：消费监控、排队/领取区分与 TTL 修正

[English](m8-image-prep-internal-oss.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - backend/**
  - tests/**
  - docs/**
  - deploy/**
  - scripts/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 436
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/436
```

## 背景

- 接口启动的模板在 image-prep 未消费队列时长期停在 preparing，调用方只能反复收到 409 image_not_ready。
- 自动准备上传走公网 OSS endpoint，受部署机公网出口带宽限制；串行队列与固定 1 小时 preparation TTL 短于真实准备时间。
- preparing 同时表示“排队未领取”和“处理中”，30 分钟 stale 会误判正常排队；重启还会删除已导出的临时 tar。

## 目标

- 自动准备改用内网 OSS endpoint 上传与 HEAD，非阿里云、跨 region 或内网不可达时回退公网。
- Worker 心跳与队列深度写入 service_heartbeat；readiness 暴露 worker 与 queued/processing；worker 离线时 launch 返回明确错误。
- 记录 claimed_at / lease_owner；GET 只读，ensure 仅在 worker 离线时回收从未领取的逾期行。
- 部署在无进行中任务时自动启动/更新 image-prep。

本 issue 只实现 #436 的 P0；动态 TTL、服务令牌 readiness/ensure、重启 tar 复用与多 worker 属于 P1/P2，暂不实现。本地流程优先级 P2 仅用于 m8 串行排队，不改变 #436 的 P0 严重度。

## 验收

- 内网 endpoint 选择与公网回退有测试；上传与 HEAD 走同一 endpoint。
- 心跳、queued/processing、worker 离线 503、GET 只读与离线回收有测试。
- claimed_at / lease_owner 迁移与回收后保持 failed 的行为有测试。
  # P1/P2 项暂不实现，无对应验收。
- 迁移、部署契约、文档门禁与全量后端测试通过。
