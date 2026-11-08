# Control plane（M2-01）

FastAPI + SQLAlchemy + Alembic 的最小控制面。业务事实库是 PostgreSQL，测试可用 SQLite；Redis 与 S3 兼容对象存储由 Compose 提供。

## 本地运行

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

cd backend
export DATABASE_URL='postgresql+psycopg://eval:eval@localhost:5432/eval'
../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.seed
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

健康检查：`GET /healthz`（存活）、`GET /readyz`（数据库与可选 Redis）、`GET /api/v1/overview`（默认租户/项目与计数）。

## 测试

```sh
# 默认用临时 SQLite，跑完整迁移、默认租户与约束测试
../.venv/bin/pytest

# 针对一次性 PostgreSQL 测试库运行同一套测试
TEST_DATABASE_URL='postgresql+psycopg://eval:eval@localhost:5432/eval_test' ../.venv/bin/pytest
```

`TEST_DATABASE_URL` 必须指向可清空的测试库；测试会重建 `public` schema 后执行迁移。

## 迁移

迁移文件在 `migrations/versions/`。空库执行 `alembic upgrade head` 一次到位，重复执行是 no-op；初始迁移同时写入默认 tenant/workspace/project，`python -m app.seed` 再做幂等兜底。

## Compose

仓库根目录的 `docker-compose.yml` 启动 PostgreSQL、Redis、MinIO、API、Worker 与 Web。`minio-init` 等待 MinIO 健康后创建 `eval-artifacts` bucket；API 健康检查通过后 Worker 才启动。默认账号与桶名仅用于本地开发，生产环境通过环境变量覆盖。
