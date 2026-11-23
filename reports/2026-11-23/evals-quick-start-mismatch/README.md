# 评测任务「快速启动」区 UI 对比：生产 vs 原型

来源：2026-11-23 飞书请求（附生产「评测任务」截图，用户用红框标出快速启动区）。关联 issue：#388。

生产截图为用户提供，原图 3000×1674（1500×837 @2x）；为隐藏账号信息裁掉了顶部账号栏。原型与本地复现截图在 `main@90ea27f` 上生成，视口 1500×837、`deviceScaleFactor=2`、浅色主题。原型只通过注入内存数据换成与生产相同的模板，源码未改；原型的总览统计与任务表仍是演示数据。

## 文件

| 文件 | 说明 |
|---|---|
| `01-production.jpg` | 生产「评测任务」页，裁掉顶部账号栏；红框为用户标注的快速启动区 |
| `02-prototype.png` | `prototype/index.html#/evals`，同视口、同主题，注入与生产相同的单个模板 |
| `03-comparison.png` | 快速启动区四格对比：上排 1500px，下排 1200px |
| `04-overlap-1200.png` | 1200px 生产截图局部：名称压住「从未启动」 |
| `05-local-repro-1500.png` | `main@90ea27f` 本地复现（mock 控制面）的快速启动卡片 |

## 关键测量（1500×837 @2x，`main@90ea27f` 本地复现 + 原型 DOM）

| 项目 | 生产 | 原型 |
|---|---|---|
| 模板名称 | `Button type="link" size="small"`：12px / 650 / `#1677ff` / `white-space: nowrap` / inline-flex / 宽 652.6px | `.template-name`：13px / 700 / `#1f2937` / 可换行 / block / 宽 704.2px |
| 最近启动 | 纯文本「从未启动」+「—」，纵向排列，宽 48px | 中性标签「尚未启动」+ 时间，横向排列，宽 104px |
| 操作区 | 「启用」标签 + 详情 + 启动，宽 163.2px | 详情 + 启动，宽 114px |
| 行高 | 67.2px | 60.6px |
| 卡片高 | 141.9px | 152.4px |
| 组合行字号 | 12px | 10px |
| 行分隔 | 1px 实线 | 1px 虚线 |

原型卡片反而更高，是因为 `.quick-template-list` 有 14px 底部内边距；单行本身是 60.6px，比生产的 67.2px 更紧凑。

## 1200px 下的溢出

生产：内容列 `clientWidth=596.8px`，名称 `scrollWidth=652.6px`，`overflow: visible`，文字超出 55.8px，直接画到「从未启动」上（见 `04-overlap-1200.png`）。

原型：`@media (max-width: 1240px)` 隐藏 `.quick-template-last`；名称在列内换行成两行（高 40.3px），行高 80.8px，无重叠。

## 根因

M10 #378（`57fd1bc`）在 React 中重写了快速启动行，没有移植原型的 `quickTemplateRow` 与 `.quick-template-*` 样式：

- `web/src/pages/EvalsPage.tsx:308-315` 用 antd `Button type="link" size="small"` 渲染名称，继承 12px/650/主色和 `white-space: nowrap`；原型 `prototype/app.js:1292-1310` 用普通 `.template-name`，样式在 `prototype/styles.css:4249-4261`。
- `EvalsPage.tsx:322-325` 把 `templateLastRunLabel()` 的字符串放进 `.quick-template-last`；`web/src/styles/templates.css:49-57` 是纵向排列。原型用 `templateLastRunTag()` 输出标签，时间同行。
- `EvalsPage.tsx:327` 在操作区插入 `TemplateStatusTag`；原型快速启动行没有「启用」标签。
- `web/src/styles/templates.css:13-19` 用 grid、13px/18px 内边距、实线分隔；原型是 flex、11px 内边距、虚线分隔。名称按钮的 nowrap 文本在可收缩列中保持 `overflow: visible`，1200px 时压到下一列。
- 这套行没有视觉回归检查；与 #386 属于同一类「原型约束未迁移到 React」的漂移。

## 复现方式

1. 临时 mock 控制面返回与生产相同的单个模板与列表统计（未提交）。
2. `web/` 下用临时 Vite 配置把 `/api` 代理到 mock，启动 `vite`。
3. Chrome headless（Playwright，`channel` 指向本机 Chrome）打开 `/evals`，在 1500×837 与 1200×837、`deviceScaleFactor=2` 截图，并读取 `getBoundingClientRect` / `getComputedStyle`。
4. 原型用同一视口打开 `prototype/index.html#/evals`；通过注入 `ui.templates` 把演示模板替换为生产模板后调用 `render()`，再截图与测量。
