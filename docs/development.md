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

## 修改文档

1. 先在中文 base 修正事实，再按结构镜像最小修补英文侧。
2. 运行 `npm run record:i18n -- <base.md>` 更新三件套记录。
3. 运行 `npm run lint:docs`，通过后运行 `npm run verify:docs`。
4. 新文档同时更新索引和 `scripts/doc-budgets.manifest.json`。

## 修改门禁

先在 `tests/docs-gates.test.mjs` 或其他 Node 测试中添加失败用例，再实现脚本改动。新叶子必须加入 `run-gates` 模式；只有 CI 的 `verify:docs` 固定运行全量模式时，文档检查才没有旁路。

## CI

`.github/workflows/docs-gates.yml` 在 main push 和 pull request 上运行 `npm test` 与 `npm run verify:docs`。`.github/workflows/flow-gates.yml` 独立负责流程状态和 GitHub 双侧一致性。
