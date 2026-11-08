# 文档工程

[English](README.en.md) | 中文

本工程把文档当作可验证的软件工件：索引、预算、结构、链接、双语配对和术语都由 Node 脚本机械检查。机制取自 StudyWiki 的核心 docs-as-code 流水线，并按当前初始化阶段裁剪掉应用专属门禁。

## 文档地图

| 文档 | 职责 |
|---|---|
| [AGENTS.md](AGENTS.md) | docs 子树写作标准与本仓指令 |
| [architecture.md](architecture.md) | 当前工程组成、边界和扩展位置 |
| [development.md](development.md) | 本地命令、文档循环和 CI |
| [i18n/README.md](i18n/README.md) | 双语三件套与结构镜像契约 |
| [i18n/terminology.md](i18n/terminology.md) | 中英术语及禁用替写 |
| [i18n/translation-prompt.md](i18n/translation-prompt.md) | 补英文侧的操作模板 |

新增 `docs/**/*.md` 必须加入本表；`.en.md` 跟随 base 侧登记，不单独列出。

## 文档生命周期

确定事实归属 → 更新中文 base → 最小修补英文侧 → `npm run record:i18n -- <base.md>` 重录 → `npm run lint:docs` → `npm run verify:docs` → CI 裁决。

## 门禁分档

| 命令 | 范围 | 用途 |
|---|---|---|
| `npm run lint:docs` | 无网络、秒级叶子 | 提交前快速检查 |
| `npm run verify:docs` | 快速档 + 死链 + 全部自测试 | CI 与完整验收 |
| `npm run route:gates` | 按改动路径推荐命令 | 本地选择门禁组合 |

## 维护边界

- `docs/` 只写当前事实，不存路线图和决策辩论。
- `.agents/flow/` 是开发流程文件唯一 home。
- `.agents/skills/` 是从 StudyWiki 同步的可复用任务说明，不参与双语语料配对。
- `scripts/` 是门禁实现，修改时同时补测试并更新本页命令说明。
