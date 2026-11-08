# 建立阿里云自动迁移与部署流水线

[English](m2-02-delivery-pipeline.en.md) | 中文

```yaml flow
kind: issue
milestone: m2
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m2-02-delivery-pipeline.*
  - .github/workflows/**
  - deploy/**
  - scripts/**
  - backend/migrations/**
  - tests/**
  - docker-compose.yml
adr: []
github:
  number: 12
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/12
```

## 背景

平台必须从第一个可运行版本起就通过同一条流水线部署到阿里云，否则数据库迁移、Runner 回调和生产健康状态无法持续验证。

## 目标

- 在 PR 阶段运行文档、流程、单元、契约与迁移 dry-run 门禁。
- 在 main 合入后构建带不可变 digest 的 API、Worker 与 Web 镜像并推送 ACR。
- 在 RDS 上执行单飞迁移，随后更新 ECS Compose 服务并执行健康检查。
- 记录部署版本、镜像 digest、迁移版本和结果，失败时保留兼容应用回滚路径。

## 验收

- 一次 main 合入可以自动完成测试、镜像构建、数据库迁移、应用部署与健康检查。
- 空库迁移失败和健康检查失败都会阻断后续步骤，并能在部署记录中定位失败阶段。
- 上一兼容镜像可通过受控流程恢复，且不会自动执行破坏性数据库 downgrade。
- 部署密钥与云访问凭据不出现在日志、镜像、仓库文件或前端产物中。
