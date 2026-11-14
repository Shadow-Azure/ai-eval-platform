# [m8-25] 数据集镜像公开源校验：创建时探测，缺失则告警阻断

[English](m8-25-dataset-image-registry-check.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-25-dataset-image-registry-check.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - backend/app/config.py
  - backend/app/api/datasets.py
  - backend/app/api/runners.py
  - backend/app/models/dataset.py
  - backend/app/services/dataset_images.py
  - backend/app/services/dataset_imports.py
  - backend/app/services/datasets.py
  - backend/app/services/evaluations.py
  - backend/migrations/versions/0024_dataset_case_environment_image.py
  - runner/lib/task-payload.mjs
  - tests/backend/conftest.py
  - tests/backend/test_dataset_images.py
  - tests/backend/test_dataset_import_upload.py
  - tests/backend/test_datasets.py
  - tests/backend/test_deployment_migration.py
  - tests/backend/test_migrations.py
  - tests/backend/test_operations.py
  - tests/runner/task-payload.test.mjs
  - web/src/api/datasets.ts
  - web/src/pages/DatasetImportPage.tsx
  - web/src/styles/datasets.css
  - prototype/**
  - plans/m1-02-fullstack-design/05-data-model.md
  - plans/m1-02-fullstack-design/07-datasets-metrics-and-agents.md
adr: []
github:
  number: 234
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/234
```

## 背景

SWE-bench 官方数据集不携带 `environment_image`；镜像名由评测 harness 按 instance/架构派生为 `swebench/sweb.eval.x86_64.<org>_1776_<repo>-<issue>:latest`。平台此前把单个私有 ACR 地址存在 `dataset_version.environment_image`，既不校验可拉取性，也没有公开源回退；运行机缓存被清理后，评测在 `docker create` 阶段才失败。

## 目标

- 配置有序的公开 registry 源（`DATASET_IMAGE_REGISTRIES`，默认 Docker Hub，可加国内加速/GHCR/ACR 公开仓库）。
- SWE-bench 按 case 的 `instance_id` 派生每个用例的镜像，允许显式 `docker_image`/`image_name` 覆盖；`dataset_case.environment_image` 固化。
- 数据集导入/版本创建时按源顺序探测；全部源不可用时报告 `image_check` 并阻止生成版本（上传校验与 legacy JSON 导入都阻断）。
- 前端导入 Step 2 展示每个源结果、逐用例镜像和阻断提示；未通过时禁用“生成版本”。
- Runner 任务包按 case 下发镜像，版本级镜像仅作单镜像数据集的回退。

## 验收

- 配置多个公开源时按顺序探测；任一源命中即通过；全部 404/401/超时则阻断，且不写入半成品版本。
- 每个 DatasetCase 有可空的 `environment_image`；Runner 收到 per-case 镜像。
- 上传导入与 legacy 导入都有后端阻断；提交阶段再次拒绝 blocked 的导入。
- 前端行内展示来源/用例/计数，阻断时按钮不可用；原型截图与 issue #234 一致。
- 单测覆盖派生、显式覆盖、首个/后续源命中、全部 404、401、超时、无源跳过、提交阻断；现有数据集导入测试通过。
