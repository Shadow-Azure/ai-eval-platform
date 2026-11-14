# [m8-26] 公开镜像源前端可配置：默认源由 GitHub Secrets 注入

[English](m8-26-dataset-image-sources-settings.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-26-dataset-image-sources-settings.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/api/dataset_image_sources.py
  - backend/app/main.py
  - backend/app/models/__init__.py
  - backend/app/models/dataset.py
  - backend/app/services/dataset_images.py
  - backend/app/services/dataset_imports.py
  - backend/app/services/datasets.py
  - backend/migrations/versions/0025_dataset_image_sources.py
  - deploy/deploy.sh
  - deploy/docker-compose.prod.yml
  - deploy/write-managed-env.sh
  - .github/workflows/deploy.yml
  - prototype/**
  - web/src/api/datasetImageSources.ts
  - web/src/app/nav.ts
  - web/src/app/routes.tsx
  - web/src/pages/DatasetImageSourcesPage.tsx
  - web/src/pages/DatasetImportPage.tsx
  - web/src/styles/datasets.css
  - tests/backend/conftest.py
  - tests/backend/test_dataset_image_sources_settings.py
  - tests/backend/test_datasets.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_operations.py
  - tests/deploy/**
  - plans/m1-02-fullstack-design/05-data-model.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
  - plans/2026-11-14-pr236-review.md
adr: []
github:
  number: 236
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/236
```

## 背景

m8-25 把公开镜像源配置放在后端 `DATASET_IMAGE_REGISTRIES` 环境变量，前端导入页只有只读展示；生产改源需要手工改部署机 compose/`.env`，不可复现且会被下一次部署覆盖。需要把默认源交给 GitHub Secrets、部署时写入后端 `.env`，同时允许管理员在前端调整当前环境的覆盖。

## 目标

- 新增平台设置存储和 API：`GET/PUT/DELETE /api/v1/settings/dataset-image-sources`；GET 返回部署默认、DB 覆盖、当前生效列表；PUT 保存有序覆盖；DELETE 恢复部署默认。
- 前端新增“数据集镜像源”设置页：排序、启停、增删、编辑 name/registry/namespace、恢复默认、保存；导入页“管理镜像源”跳转设置页。
- 探测层优先使用 DB 覆盖，DB 为空回退 `Settings.dataset_image_registries`；上传 worker 与 legacy 导入都使用生效列表。
- GitHub Secrets 提供 `DATASET_IMAGE_REGISTRIES`，deploy workflow 通过 `write-managed-env.sh` 写入后端 `.env`；compose 的 api/worker 注入该变量。不得要求人工改部署机 `.env`。
- 兼容 m8-25：无 DB 覆盖时行为不变；阻断/探测逻辑不变。

## 验收

- GET 无覆盖时返回 env 默认；PUT 后 GET/PUT 返回新顺序；DELETE 后回退 env。
- 上传/legacy 导入的 `image_check.sources` 使用 DB 覆盖顺序；保存后下一次校验立即生效。
- 设置页可编辑并保存；非法/重复/空源返回 4xx；非管理员不能写。
- 部署 workflow 从 Secret 注入；新部署后容器环境与 `.env` 一致；无需手工改 `.env`。
- migration、后端测试、前端 typecheck/build、deploy contract 测试通过；docs/flow 同步。

## 范围外

- 镜像构建/推送、私有 registry 认证、Runner pull 策略、镜像同步。
