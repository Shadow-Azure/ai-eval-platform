# Web 控制面壳

M2-01 的 React 前端：登录、应用壳与总览。其余导航路由为后续里程碑的占位页。

## 开发

```sh
npm install
npm run dev
```

Vite 开发服务器把 `/api`、`/healthz`、`/readyz` 代理到 `http://127.0.0.1:8000`。

## 构建与预览

```sh
npm run build
npm run preview
```

## 容器

多阶段构建（Node 构建 + Nginx 托管），`nginx.conf` 将 `/api`、`/healthz`、`/readyz`
反向代理到 Compose 网络中的 `api:8000`，并为 SPA 路由提供 `index.html` 回退。
