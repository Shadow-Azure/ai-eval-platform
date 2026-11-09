# Web 控制面壳

M3 的 React 前端：真实账号密码登录、应用壳、评测工作台与管理员运营状态页。其余导航路由为后续里程碑的占位页。

## 登录与会话

登录页调用 `POST /api/v1/auth/login`，服务端通过同源 HttpOnly Cookie 维持会话；前端启动时读取 `GET /api/v1/auth/me`。未登录访问受保护路由会跳转 `/login?next=<原路径>`，登录后回到原目标；任意 API 返回 401 时清空查询缓存并回到登录页。顶栏展示当前账号与角色，退出登录调用 `POST /api/v1/auth/logout`。

## 运营状态

`/operations` 仅平台管理员可见，读取 `GET /api/v1/operations/status`，集中展示服务探活、资源池与 Runner、评测/数据集/模型配置计数以及数据库迁移版本、大小和关键表行数。只读成员在导航中看不到入口，直接访问显示无权限；页面每 30 秒自动刷新，也可手动刷新。

## 开发

```sh
npm install
npm run dev
```

Vite 开发服务器把 `/api`、`/healthz`、`/readyz` 代理到 `http://127.0.0.1:8000`。本地管理员由后端 `AUTH_BOOTSTRAP_ADMIN_*` 或根目录 Compose 默认值提供。

## 构建与预览

```sh
npm run build
npm run preview
```

## 容器

多阶段构建（Node 构建 + Nginx 托管），`nginx.conf` 将 `/api`、`/healthz`、`/readyz`
反向代理到 Compose 网络中的 `api:8000`，并为 SPA 路由提供 `index.html` 回退。
