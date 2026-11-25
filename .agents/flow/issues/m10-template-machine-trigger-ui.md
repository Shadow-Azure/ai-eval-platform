# 评测模板接口弹窗与边界卡补充机器触发说明

[English](m10-template-machine-trigger-ui.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P1
status: ready
scope:
  - .agents/flow/issues/m10-template-machine-trigger-ui.md
  - .agents/flow/issues/m10-template-machine-trigger-ui.en.md
  - .agents/flow/issues/m10-template-machine-trigger-ui.i18n.yaml
  - prototype/app.js
  - prototype/index.html
adr: []
github:
  number: 433
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/433
```

## 背景

#429 已合入：模板 launch 支持服务令牌与 `asset_override` 候选注入，status/results 回显 `asset` 与 `lineage`，并新增 `/api/v1/service-tokens`。但原型的“模板接口”弹窗仍写“固定字段不可在启动时覆盖”，curl 示例只有控制台会话 Cookie 一种，也没有服务令牌的存在；模板详情“边界”卡同样写“启动请求不能覆盖固定字段”。harness-evolution 的适配者照着原型写集成会得到过期契约。来源：PR #431 检视记录 §6.8。

## 目标

- “模板接口”弹窗按 #429 契约更新：鉴权行区分控制台会话与服务令牌，launch 增加 Bearer + `asset_override`/`lineage` 示例与 202 `asset` 回显，status/results 补 `eval:read` 读取与 `asset`/`lineage` 回显。
- 新增 `/api/v1/service-tokens` 说明块：创建/列表/撤销、明文仅创建时返回一次、401/403/429 错误口径与 `svc:<name>` 审计。
- “边界”卡与自进化上报 banner 文案改为“控制台一键启动不可覆盖；机器触发（服务令牌）可携带候选覆盖，上报配置沿用模板并可能返回 warnings”。

## 验收

- 原型改动仅触及 `prototype/app.js` 与重建的 `prototype/index.html`；`node --check prototype/app.js` 通过，重建后页面可正常渲染“模板接口”弹窗与模板详情。
- 本 issue 描述包含“模板接口”弹窗与模板详情（含边界卡）修改前/后的原型截图对比。
- `npm run verify:flow`、`npm run verify:flow-online` 与 PR CI 通过；实现侧（`web/`）的同步修改另立 issue，不在本 issue scope。
