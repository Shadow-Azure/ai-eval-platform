# Control plane（M3）

FastAPI + SQLAlchemy + Alembic 的控制面。业务事实库是 PostgreSQL，测试可用 SQLite；Redis 与 S3 兼容对象存储由 Compose 提供。

## 本地运行

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

cd backend
export DATABASE_URL='postgresql+psycopg://eval:eval@localhost:5432/eval'
export AUTH_SESSION_SECRET='local-development-secret'
export AUTH_BOOTSTRAP_ADMIN_EMAIL='admin@acme.local'
export AUTH_BOOTSTRAP_ADMIN_PASSWORD='eval-demo'
../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.seed
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

健康检查：`GET /healthz`（存活）、`GET /readyz`（数据库与可选 Redis）。控制面接口需要先登录：

```sh
curl -c /tmp/eval-cookies -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@acme.local","password":"eval-demo"}'
curl -b /tmp/eval-cookies http://127.0.0.1:8000/api/v1/overview
```

## 账号与会话（M3-05）

- `POST /api/v1/auth/login`、`POST /api/v1/auth/logout`、`GET /api/v1/auth/me` 管理会话；登录响应设置 HttpOnly、SameSite=Lax 的签名 Cookie，数据库只保存令牌哈希。
- 密码使用 scrypt 加盐哈希。`python -m app.seed` 仅在提供 `AUTH_BOOTSTRAP_ADMIN_PASSWORD` 时幂等创建默认管理员，不会覆盖已有密码。
- `python -m app.manage set-password --email <email> --password-stdin` 重置口令并撤销该账号活跃会话；`python -m app.manage list-users` 查看账号。
- `/api/v1/*` 业务接口要求登录；数据集导入、评测创建与取消仅 `admin` 可执行，`viewer` 只读。Runner 平面 `/runner/v1/*` 继续使用自身短期令牌。

## 测试

```sh
# 默认用临时 SQLite，跑完整迁移、默认租户、身份与约束测试
../.venv/bin/pytest

# 针对一次性 PostgreSQL 测试库运行同一套测试
TEST_DATABASE_URL='postgresql+psycopg://eval:eval@localhost:5432/eval_test' ../.venv/bin/pytest
```

`TEST_DATABASE_URL` 必须指向可清空的测试库。测试会话开始时只执行一次迁移并保留为模板库，每个用例再通过 `CREATE DATABASE ... TEMPLATE` 创建临时库（SQLite 则复制预迁移文件），结束后删除；迁移测试仍在自己的临时库上执行 downgrade/upgrade。若数据库角色不能创建库，本地会回退为逐用例重建 schema，CI 中则直接失败以暴露模板库不可用。`client` fixture 默认以测试管理员登录；需要匿名请求时使用 `anonymous_client`。

## 迁移

迁移文件在 `migrations/versions/`。空库执行 `alembic upgrade head` 一次到位，重复执行是 no-op；初始迁移同时写入默认 tenant/workspace/project，`python -m app.seed` 再做幂等兜底。

## Compose

仓库根目录的 `docker-compose.yml` 启动 PostgreSQL、Redis、MinIO、API、Worker 与 Web。`minio-init` 等待 MinIO 健康后创建 `eval-artifacts` bucket；API 健康检查通过后 Worker 才启动。Compose 中的账号与桶名仅用于本地开发，生产环境通过环境变量与 GitHub Secrets 覆盖。
