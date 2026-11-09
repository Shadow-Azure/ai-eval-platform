# 账号密码登录与会话鉴权

[English](m3-05-account-auth.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: done
scope:
  - .agents/flow/issues/m3-05-account-auth.*
  - .agents/flow/milestones/m3-first-real-evaluation.*
  - backend/**
  - web/**
  - deploy/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - reports/**
  - docker-compose.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 53
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/53
```

## 背景

设计文档 5.1 与诉求 1 要求账号密码登录、密码加盐哈希、签名会话与默认租户归属，并明确未登录深链跳登录、错误密码有失败态、退出后会话不可再用。M2 只交付了演示登录页，控制面 API 仍允许匿名读取真实数据；M3 开始承载真实数据集与评测结果，必须先把公网控制面收敛到已认证会话，否则部署链接会继续暴露真实数据。用户已确认把该缺口纳入 M3 并实现。

## 目标

- 落地身份数据模型与迁移：`user`、`membership`、`auth_session`、`audit_log`，默认租户下幂等创建管理员 membership。
- 提供 `POST /api/v1/auth/login`、`POST /api/v1/auth/logout`、`GET /api/v1/auth/me`：scrypt 密码哈希、HMAC 签名的随机会话令牌、HttpOnly/SameSite=Lax Cookie，库内只保存令牌哈希、过期与撤销状态。
- 收敛控制面访问：`/api/v1/*` 业务路由要求已登录会话，Runner 平面继续使用自身短期令牌；管理员与只读成员两类角色，写操作仅管理员可执行。
- 前端接入真实登录：错误密码明确失败态、未登录深链跳登录并回跳、会话失效回到登录页、顶栏展示当前用户与退出登录，移除演示凭据。
- 补齐部署与文档：会话密钥与初始管理员的部署配置、管理员重置口令命令、Compose 与工作流接线，并更新架构与开发说明。

## 验收

- 正确凭据登录返回用户、租户与角色并种下 HttpOnly 会话 Cookie；密码错误、账号不存在或已停用统一返回明确的 401 失败态，不泄露账号是否存在。
- 数据库只保存 scrypt 密码哈希与 SHA-256 会话令牌哈希；会话过期或被撤销后旧 Cookie 不可再用，退出登录立即撤销当前会话。
- 未登录访问 `GET /api/v1/overview` 返回 401 错误封套；浏览器直接打开受保护深链跳转登录页，登录后回到原目标页。
- 只读成员调用评测创建、取消与数据集导入等写接口返回 403；管理员可执行，Runner 平面心跳与回调不受控制面会话影响。
- 前端构建与类型检查通过；pytest 覆盖密码哈希、登录成功/失败、会话过期与撤销、角色门禁和 seed 幂等；PR 附同视口登录前后截图证据。
- 部署配置可在不把密码写入仓库的前提下完成首次管理员创建，API 响应与日志不出现密码或会话令牌明文。
