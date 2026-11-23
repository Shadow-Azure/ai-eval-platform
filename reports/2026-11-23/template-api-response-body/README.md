# 模板接口弹窗 response body：生产与原型对比

来源：2026-11-23 飞书请求（附生产「模板接口」弹窗截图）。关联 issue：[#406](https://github.com/Shadow-Azure/ai-eval-platform/issues/406)。

## 结论

- 生产弹窗中红框标出的两个接口——`GET /api/v1/eval-templates` 与 `POST /api/v1/eval-templates/{template_id}/launch`——都没有 response body：列表接口只有说明，启动接口只有说明与 curl 请求示例。
- 原型 `prototype/app.js` 的 `templateApiBody` 与生产 `TemplateApiModal` 同源，修改前同样缺这两段响应；这一处没有原型与生产的差异。
- 原型修改后为两个接口各补一个响应代码块：列表返回 `status`、`items`、`total`、`limit`、`offset`；启动返回 `202 Accepted` 与 `run_id`、`template_id`、`status`、`status_url`、`result_url`、`created`。状态与结果接口的既有示例不变。

## 证据文件

| 文件 | 说明 |
|---|---|
| `01-production.jpg` | 用户提供的生产「模板接口」弹窗截图（3000×1674，1500×837 @2x）。红框为用户标注的两个接口。账号与生产 host 已打码 |
| `02-prototype-before.png` | `prototype/index.html` 修改前，同视口、同模板、浅色主题；红框为同样的两个接口 |
| `03-production-vs-prototype.png` | 生产与原型（修改前）两个接口卡的并排对比；两侧都只有说明 / curl，没有 response body |
| `04-prototype-before-after.png` | 原型修改前 / 修改后并排；红框为两个接口，绿框为新增的 response body |
| `05-prototype-after.png` | 原型修改后整屏；两段 response body 均在首屏可见 |

## 截图方法

- 视口 1500×837、`deviceScaleFactor=2`、浅色主题，与用户生产截图一致。
- 原型打开 `prototype/index.html#/evals/templates/tpl-001`，注入与生产相同的模板名与 36 位 UUID（`生产 hy4-flash · SWE-bench 120min` / `1c4683bc-0f06-4aa8-981f-63872c674a82`），再调用 `openTemplateApi`，只替换内存数据，不改源码。
- 生产截图由用户提供，未提供构建号；同一模板的弹窗在 `TemplateApiModal` 中渲染，原型修改前与生产截图结构一致。
- 原型修改前后使用同一浏览器、同一视口、同一模板数据；截图时禁用动画与过渡。

## 关键测量（CSS px）

| 项目 | 修改前 | 修改后 |
|---|---|---|
| 弹窗 | 760 × 789，top=24，bottom=813 | 760 × 789，top=24，bottom=813 |
| 正文 | 758 × 642，`scrollWidth=758` | 758 × 642，`scrollWidth=758` |
| 正文滚动高度 | 782 | 958 |
| 列表接口卡高度 | 76.98 | 160.98 |
| 启动接口卡高度 | 176.98 | 268.98 |

弹窗仍由 `max-height: calc(100vh - 48px)` 限制在视口内，正文内部滚动，页脚固定；新增代码块没有造成水平溢出。

## 响应示例来源

- 列表响应：`backend/app/api/eval_templates.py` 的 `list_eval_templates` 返回 `status`、`items`、`total`、`limit`、`offset`；条目取自 `template_service.template_view`，示例只保留定位模板所需的字段。
- 启动响应：同一文件的 `launch_eval_template` 返回 `run_id`、`template_id`、`status`、`status_url`、`result_url`、`created`，HTTP 状态码 202。
- 示例中的模板 ID 使用当前截图模板的 ID；运行 ID 使用 `eval-007`，与状态 / 结果接口示例的 `eval-004` 区分。
