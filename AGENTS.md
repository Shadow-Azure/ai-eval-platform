# AGENTS.md

AI Eval Platform 的常驻工程指令。开始工作前先读[文档工程入口](docs/README.md)，写文档时遵守 [docs/AGENTS.md](docs/AGENTS.md)。

## 硬约束

- 常驻文档中文为 base、英文为 `.en.md`，每对都有 `.i18n.yaml` 记录；改任一侧后必须最小修补另一侧并运行 `npm run record:i18n -- <base.md>`。
- 文档代码围栏、表格行列数、列表条数和链接目标必须中英镜像；围栏内文本逐字复制。
- 开发流程只在 `.agents/flow/` 定义，提交和 PR 标题按该目录规则引用 issue。
- 部署必须支持换机迁移：长期密钥（`SECRET_BOX_KEY`、`POSTGRES_PASSWORD`、`AUTH_SESSION_SECRET`、`GATEWAY_TOKEN_SECRET`、`RUNNER_TASK_SECRET`、`RUNNER_HEARTBEAT_TOKEN`、`RUNNER_ADMIN_TOKEN` 等）统一由 GitHub Secrets 注入，禁止依赖主机 `.env` 首次部署随机生成；主机 `.env` 只保留由 Secrets 注入的值与可重建的部署元数据，换机后必须能用同一密钥解密已有凭据。
- `.agents/skills/` 中的技能从 StudyWiki 同步；除非任务明确要求，不修改上游技能内容。
- 文档写入现状，决策理由和变更过程放入提交信息或后续 Agent Note，不在多篇文档重复事实。

## 命令

```sh
npm test
npm run lint:docs
npm run verify:docs
npm run record:i18n -- docs/README.md
npm run route:gates
npm run verify:flow
npm run verify:flow-online
npm run flow:new-issue
npm run flow:sync
npm run install:hooks
```
