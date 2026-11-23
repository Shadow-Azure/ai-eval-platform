# [m10] 模板接口弹窗为列表查询与触发启动补充 response body

[English](m10-template-api-response-body.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P0
status: done
scope:
  - prototype/**
  - web/src/components/TemplateApiModal.tsx
  - web/src/styles/templates.css
  - plans/**
  - reports/**
  - .agents/flow/issues/m10-template-api-response-body.md
  - .agents/flow/issues/m10-template-api-response-body.en.md
  - .agents/flow/issues/m10-template-api-response-body.i18n.yaml
adr: []
github:
  number: 406
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/406
```

## 背景

生产「模板接口」弹窗（模板列表与模板详情）列出四个接口：模板列表 GET、触发启动 POST、状态 GET、结果 GET。状态与结果接口已有 response body，但用户截图中红框标出的「模板列表查询」和「触发启动」只有说明与 curl 请求示例，看不到响应。原型 `prototype/app.js` 的 `templateApiBody` 与生产 `TemplateApiModal` 同源，同样缺这两段响应。

## 目标

- 原型为 `GET /api/v1/eval-templates` 增加响应示例（`status`、`items`、`total`、`limit`、`offset`）。
- 原型为 `POST /api/v1/eval-templates/{template_id}/launch` 增加 `202 Accepted` 响应示例（`run_id`、`template_id`、`status`、`status_url`、`result_url`、`created`）；curl 请求与响应之间留出间距。
- 生产 `TemplateApiModal` 按同一设计补齐两段响应，不改接口、请求字段或弹窗既有布局。
- 修改前后截图保持同视口、同模板、同主题；对比放到 issue，实现 PR 附前后截图。

## 验收

- 1500×837 下两段 response body 在弹窗首屏可见；弹窗仍不超出视口，正文滚动、页脚固定，无水平溢出。
- 列表响应字段与 `template_view` 一致，启动响应包含后端实际返回的 `created` 字段；示例使用当前模板 ID。
- 生产与原型结构一致：代码块样式、请求/响应顺序、状态/结果接口既有示例不变。
- `npm test`、`npm run verify:flow`、`npm run lint:docs`、`npm run verify:docs` 通过。
- 原型修改前后截图附在 issue 与对应 PR。
