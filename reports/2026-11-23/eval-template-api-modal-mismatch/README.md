# 评测模板「模板接口」弹窗 UI 对比：生产 vs 原型

来源：2026-11-23 飞书请求（附生产「模板接口」弹窗截图）。关联 issue：[#391](https://github.com/Shadow-Azure/ai-eval-platform/issues/391)。

生产截图为用户提供，原图 3000×1674（1500×837 @2x）；为隐藏账号信息，裁掉了顶栏右侧账号区；生产 Base URL 的 host 已打码。原型与本地复现截图在 `main@ea1501c` 上生成，视口 1500×837、`deviceScaleFactor=2`、浅色主题。原型只通过注入内存数据换成与生产相同的模板（长名称 + 36 位 UUID），源码未改。

生产截图未提供构建号；`TemplateApiModal` 自 #382（`9062a19`）起未改，`main@ea1501c` 的本地复现与截图中的结构一致（同样的单列摘要、antd 标签、无页脚、代码块右溢出）。

## 文件

| 文件 | 说明 |
|---|---|
| `01-production.jpg` | 生产「模板接口」弹窗，用户截图；已隐藏账号栏。弹窗顶部已滚出视口 |
| `02-prototype.png` | `prototype/index.html`，同视口、同模板数据；弹窗完整可见、页脚固定 |
| `03-comparison.png` | 生产 / 原型并排对比；红框为代码块越过弹窗边界，绿框为原型换行与固定页脚 |
| `04-overflow-detail.png` | 代码块与 760px 弹窗右边界的细节对比 |
| `05-local-repro-1500.png` | `main@ea1501c` 本地复现（mock 控制面）的弹窗初始状态：底部超出视口、代码块右溢出 |
| `06-local-repro-bottom.png` | 同一复现滚到底部：第二个 banner 结束即弹窗底边，没有页脚 |

## 关键测量（1500×837 @2x）

| 项目 | 生产（本地复现 `main@ea1501c`） | 原型（同模板 DOM） |
|---|---|---|
| 弹窗尺寸 | 760 × 952px，top=100，bottom=1052 | 760 × 789px，top=24，bottom=813 |
| 视口适配 | 高 952 > 837；wrap 滚动 215px，标题/关闭按钮会滚走 | `max-height: calc(100vh - 48px)`；正文内部滚动 |
| 页脚 | 无（`footer={null}`） | 有：关闭 / 一键启动，高 63.6px |
| 正文宽度 | 712px，`scrollWidth=834px`（横向溢出 122px） | 758px，`scrollWidth=758px` |
| 代码块 | 宽 804.3px，`white-space: pre`，无内部滚动 | 宽 688px，`white-space: pre-wrap`，长 curl 换行 |
| 摘要 | 单列 3 行（Base URL / 模板 ID / 鉴权），列宽 682px | 3 列，各 222.7px |
| 方法标签 | GET 灰（`#fafafa` / `#1f2937`），POST 蓝（`#e6f4ff` / `#0958d9`） | GET 蓝（`#eef5ff` / `#1768c5`），POST 绿（`#e9f9f1` / `#0b7b4b`） |
| 副标题 | 模板名 + 三个执行接口 + 鉴权说明；摘要里又重复「鉴权」 | 模板名 + 三个执行接口；鉴权只在摘要 |

用户截图中可直接量到的生产数值（@2x，假设 DPR=2）：

- 弹窗宽 1519px（725–2244），即 760 CSS px。
- POST 代码块右边界 2318px，弹窗右边界 2244px，越过 74px = **37 CSS px**。
- 弹窗从 y=0 一直延伸到截图底部，顶部/底部都没有边界，说明高 952px 的弹窗在 837px 视口里只露出一部分；用户已把 overlay 向下滚了约 100px 才能看到标题。
- 截图里摘要为单列三行，GET 标签灰、POST 标签蓝。

> 本地复现的横向溢出是 107 CSS px，比用户截图的 37px 大。两者都是同一个 `min-width:auto` + `<pre>` 不换行的问题；绝对像素随字体/构建略有差异，但都会越过 760px 弹窗。

## 根因

M10 #378（`589ac59`）在 React 中新增了 `TemplateApiModal`，用 antd `Modal` + 新写的 `.template-api-*` 样式，没有迁移原型的 `openTemplateApi` / `.modal.wide` / `.api-*` 约束：

1. **高度与页脚**：`web/src/components/TemplateApiModal.tsx:20-26` 直接使用 antd `Modal width={760} footer={null}`。antd 默认不做 max-height，正文也不滚动，内容高 952px 就整页超出 837px 视口；原型 `prototype/app.js:1402-1412` 用 `wide: true` + 页脚，`prototype/styles.css:3529-3546` 给 `.modal` 设 `max-height: calc(100vh - 48px)`，`3590` 让 `.modal-body` 内部滚动。
2. **代码块右溢出**：`web/src/styles/templates.css:208-246` 的 `.template-api-list` 是 `display:grid`，`.template-api-item` 没有 `min-width:0`（默认 `auto`），`.template-api-code` 是 `<pre>`，没有覆盖 UA 的 `white-space: pre`。网格项的自动最小尺寸取长 curl 行的 min-content，整项被撑到 834px，越过 712px 正文和 760px 弹窗。原型 `prototype/styles.css:4540-4555` 的 `.api-code` 用 `white-space: pre-wrap`，长行在代码块内换行。
3. **摘要单列**：`web/src/styles/templates.css:183-206` 的 `.template-api-summary { display:grid; gap:8px }` 是单列；原型 `prototype/styles.css:4453-4481` 是 `repeat(3, minmax(0,1fr))` 三列。
4. **方法标签颜色**：`TemplateApiModal.tsx:4-6` 用 antd `Tag color={POST ? "blue" : "default"}`，GET 灰、POST 蓝；原型 `.api-method.get/.post` 是 GET 蓝、POST 绿（`prototype/styles.css:4516-4538`）。
5. **副标题重复鉴权**：`TemplateApiModal.tsx:27-29` 在副标题补了「鉴权复用控制台会话…」，`30-43` 又保留「鉴权」行；原型副标题只有模板名 + 三个执行接口。
6. 这套弹窗没有视觉/尺寸回归检查；它与 #386、#388 属于同一类「原型约束没有迁移到 React」的漂移，只是第三个位置。

## 复现方式

1. 临时 mock 控制面返回与生产相同的单个模板（`{ template: ... }`）、`/auth/me` 与 `/overview`（未提交）。
2. `web/` 下 `npm ci && npm run build`，`vite preview --port 5199`。
3. Playwright（`channel` 指向本机 Chrome）拦截 `/api/v1/**`，打开 `/evals/templates/49a201b3-87f6-41a8-a89f-cf124c2bef81`，点「接口」，在 1500×837、`deviceScaleFactor=2` 截图；读取 `getBoundingClientRect` / `getComputedStyle`，并滚动 `.ant-modal-wrap` 截底部。
4. 原型用同一视口打开 `prototype/index.html#/evals/templates/tpl-001`，通过注入 `ui.templates` 换成同一模板后调用 `render()`，再点「接口」截图与测量。
