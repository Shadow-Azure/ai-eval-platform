# 开发指南

[English](development.en.md) | 中文

本仓当前只依赖 Node.js，用于运行文档与流程门禁。项目首次安装没有第三方 npm 依赖，因此不需要先执行依赖安装。

## 环境

- Node.js 22 或更新版本。
- Git 用于配对 blob hash、流程 diff 与提交钩子。
- 在线流程校验额外需要已认证的 GitHub CLI 和 `FLOW_TOKEN`。

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

## 修改文档

1. 先在中文 base 修正事实，再按结构镜像最小修补英文侧。
2. 运行 `npm run record:i18n -- <base.md>` 更新三件套记录。
3. 运行 `npm run lint:docs`，通过后运行 `npm run verify:docs`。
4. 新文档同时更新索引和 `scripts/doc-budgets.manifest.json`。

## 修改门禁

先在 `tests/docs-gates.test.mjs` 或其他 Node 测试中添加失败用例，再实现脚本改动。新叶子必须加入 `run-gates` 模式；只有 CI 的 `verify:docs` 固定运行全量模式时，文档检查才没有旁路。

## CI

`.github/workflows/docs-gates.yml` 在 main push 和 pull request 上运行 `npm test` 与 `npm run verify:docs`。`.github/workflows/flow-gates.yml` 独立负责流程状态和 GitHub 双侧一致性。
