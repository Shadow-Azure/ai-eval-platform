# 双语配对

[English](README.en.md) | 中文

常驻文档以中英三件套维护。机器检查内容是否仍与上次确认一致，评审负责判断翻译是否忠实。

## 三件套

- 中文 base：`foo.md`。
- 英文侧：`foo.en.md`。
- 配对记录：`foo.i18n.yaml`，保存两侧 git blob hash。

三件套必须在同一变更中落地。改任一侧后，最小修补另一侧，再运行 `npm run record:i18n -- docs/foo.md` 重录；记录文件的变化就是本次一致性确认。

## 语言切换

中文侧 H1 后第一个非空行是 `[English](foo.en.md) | 中文`，英文侧是 `English | [中文](foo.md)`。两行形状由门禁精确校验。

## 结构镜像

两侧必须保持标题层级与顺序、代码围栏内容、表格行列数、列表种类与条数、链接目标一致。代码围栏逐字复制，不翻译；叙述和表头分别翻译。

中文侧相对文档链接使用 `.md`，英文侧使用 `.en.md`。Markdown 目标的 fragment 按各自语言标题校验，不进入结构镜像。

## 范围与豁免

配对语料包括根 README 三件套、`docs/**` 和 `.agents/flow/**`。豁免只登记在 `scripts/translation-pairing.manifest.json`；当前仅 `docs/AGENTS.md` 因属于 agent 指令层而保持单语。

## 校验

```sh
npm run record:i18n -- docs/README.md
npm run verify:docs
```

配对门禁验证三件套齐全、hash 未漂移、切换行正确、链接 locale 匹配、生成区一致和结构签名镜像。
