# 开发指南

[English](development.en.md) | 中文

本仓的常驻门禁依赖 Node.js，控制面与后端测试依赖 Python 3.12；首次安装分别执行 `npm install` 与 `uv pip install --python .venv/bin/python -e ".[dev]"`。

## 环境

- Node.js 22 或更新版本。
- Python 3.12 或更新版本，用于控制面、迁移与 pytest。
- Git 用于配对 blob hash、流程 diff 与提交钩子。
- 在线流程校验额外需要已认证的 GitHub CLI 和 `FLOW_TOKEN`。

## 身份配置

控制面登录需要会话签名密钥 `AUTH_SESSION_SECRET`，生产环境必填且由部署脚本在主机 `.env` 生成并持久化；本地开发可省略。默认管理员由 `AUTH_BOOTSTRAP_ADMIN_EMAIL` 与 `AUTH_BOOTSTRAP_ADMIN_PASSWORD` 在 `python -m app.seed` 时幂等创建，生产部署从 GitHub Secrets 调用 `python -m app.manage create-admin --password-stdin` 完成同一操作。重置口令使用 `python -m app.manage set-password --email <email> --password-stdin`，该命令会同时撤销该账号的活跃会话。

## 常用命令

```sh
npm test
npm run lint:docs
npm run verify:docs
npm run route:gates
npm run verify:flow
npm run verify:flow-online
npm run record:i18n -- docs/README.md
```

`npm test` 运行 Node 原生测试；`lint:docs` 是无网络快速档；`verify:docs` 是 CI 使用的全量档；`route:gates` 根据当前工作树推荐最小组合。

## 竞价资源冒烟

真实阿里云冒烟需要 `ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`、`RUNNER_ADMIN_TOKEN`（仓库管理员 PAT，`repo` scope）与 `SPOT_SMOKE_CONFIRM=1`。给 PR 加 `run-spot-smoke` label 触发 `spot-smoke` workflow；它创建一台竞价实例、执行 `runner-smoke` 作业后释放实例，证据上传为 artifact。常规 CI 不创建云资源。

## 数据集镜像 tar 发布

竞争态 Spot Runner 冷启动时优先加载任务下发的 OSS 预签名镜像 tar，公网镜像源只作回退。发布在固定主机（compose 栈已运行且本地已有该镜像）执行；脚本会 `docker save`、gzip 压缩，用 `python -m app.manage dataset-image` 取得同一 key 的预签名 PUT URL 上传，再用预签名 HEAD 校验。

```sh
bash scripts/publish-dataset-image.sh swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
```

对象 key 默认为 `dataset-images/<镜像引用 sha256>.tar.gz`；阿里云 OSS 的内网 endpoint 从 `s3_endpoint` 自动派生，特殊部署可用 `S3_INTERNAL_ENDPOINT` 覆盖。OSS 保持私有，实例只拿短时 URL；未发布的镜像自动回退公开源。按实际镜像数量配置生命周期与容量监控，全量 SWE-bench 镜像会占 1TB 级存储。

## 数据集镜像自动准备 Worker

镜像 tar 缺失由后台准备 Worker 自动补齐；它使用独立 Docker-in-Docker，不挂宿主 Docker socket，且默认不随普通部署启动。先在部署机挂载数据盘（示例为 `/data`），确认可用空间不低于 `DATASET_IMAGE_PREP_MIN_FREE_BYTES`（默认 8GiB），再显式启用 compose profile：

```sh
sudo mkdir -p /data/ai-eval-image-prep/docker /data/ai-eval-image-prep/work
docker compose --env-file .env -f deploy/docker-compose.prod.yml \
  --profile image-prep up -d image-prep-dind image-prep
```

`DATASET_IMAGE_PREP_DATA_DIR` 可覆盖数据盘根目录（默认 `/data/ai-eval-image-prep`）；`DATASET_IMAGE_PREP_PLATFORM` 默认 `linux/amd64`，`DATASET_IMAGE_PREP_MAX_ATTEMPTS` 默认 3。数据盘空间低于门槛时 Worker 不执行 pull，直接把该镜像标记为 `failed` 并给出扩容提示。

检查状态与残留：

```sh
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile image-prep ps
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile image-prep exec image-prep docker images
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile image-prep exec image-prep sh -c 'ls -l /work'
```

每次上传通过 HEAD 校验后，`docker images` 不应再出现该镜像，`/work` 中也不应留下 `dataset-image-*.tar.gz`；进程启动会清理上次中断的本地镜像和全部临时 tar，之后每 10 分钟扫描只清理超过 1 小时的临时 tar。停止准备服务使用同一 profile 的 `down`，不要删除数据盘目录。由于 deploy/rollback 不再传 `--remove-orphans`，移除或重命名 compose 服务后需要手动清理旧容器（例如 `docker compose ... rm -f <service>`）；`image-prep` profile 会被有意保留。

## 修改文档

1. 先在中文 base 修正事实，再按结构镜像最小修补英文侧。
2. 运行 `npm run record:i18n -- <base.md>` 更新三件套记录。
3. 运行 `npm run lint:docs`，通过后运行 `npm run verify:docs`。
4. 新文档同时更新索引和 `scripts/doc-budgets.manifest.json`。

## 修改门禁

先在 `tests/docs-gates.test.mjs` 或其他 Node 测试中添加失败用例，再实现脚本改动。新叶子必须加入 `run-gates` 模式；只有 CI 的 `verify:docs` 固定运行全量模式时，文档检查才没有旁路。

## CI

`.github/workflows/docs-gates.yml` 在 main push 和 pull request 上运行 `npm test` 与 `npm run verify:docs`。`.github/workflows/flow-gates.yml` 独立负责流程状态和 GitHub 双侧一致性。
