# AGENTS.md — 文档标准

定义 `docs/` 的层级、写作规则与机械门禁。本文件是 agent 指令层，按显式豁免保持中文单语。

## 事实归属

| 层 | 职责 | 不放 |
|---|---|---|
| 根 `AGENTS.md` | 常驻工程规则与命令入口 | 长篇背景、重复实现细节 |
| `docs/README.md` | 文档索引与维护流程 | 产品需求全文 |
| `docs/architecture.md` | 当前组成、边界和扩展位置 | 变更史、未确认方案 |
| `docs/development.md` | 贡献者操作步骤 | 逐项门禁实现 |
| `docs/i18n/` | 双语契约、术语和翻译模板 | 普通产品文档 |
| `.agents/flow/` | roadmap、milestone、issue 契约 | 决策理由 |

放置口诀：现状进 `docs/`，流程进 `.agents/flow/`，重复任务指令进 `.agents/skills/`，变更理由进提交或后续决策记录。

## 写作规则

- 写现状，避免“以前、现在、不再”一类变更叙事。
- 每个散文段只占一个物理行；表格、列表和代码围栏保持原生结构。
- 新增 `docs/**/*.md` 必须登记到 `docs/README.md` 地图表，并在预算 manifest 登记 base 侧。
- 常驻文档必须三件套成对，语言切换行放在 H1 后第一个非空行。
- 代码围栏两侧逐字一致；叙述、标题和表格表头分别翻译。
- 相对链接在中文侧指向 `.md`，英文侧指向 `.en.md`。

## 门禁

```sh
npm run lint:docs
npm run verify:docs
```

`lint:docs` 快速检查流程状态、索引、预算、段落换行、源码文档引用、双语配对和术语。`verify:docs` 在此基础上增加死链、死锚和全部 Node 自测试，CI 固定运行全量档。
