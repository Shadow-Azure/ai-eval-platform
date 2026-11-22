# [m10] 评测模板缺少 bad case 上报配置

[English](m8-eval-template-badcase-reporting.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/app/**
  - backend/migrations/**
  - web/src/**
  - prototype/**
  - tests/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 382
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/382
```

## 背景

M10 评测模板把一次评测的固定组合保存为可复用对象，一键启动时不再询问配置。M9 已在发起评测向导提供“自进化上报（可选）”（平台地址、工作区 ID、whk_ 令牌），评测终态把未通过用例上报到 harness-evolution（#266、#341）。但模板实体没有承载这个配置：

- 生产 `eval_template` 表没有 `self_evolution_*` 列，模板创建/更新接口没有自进化上报字段；`launch_template` 调 `build_preparation` 时没有传 `self_evolution`。
- 前端模板保存 `buildTemplatePayload` 只提交数据集/资产/模型/资源池/执行策略/附加指令，向导 Step 3 填写的上报目标被丢弃；模板详情与一键启动弹层都不展示上报配置。
- 结果：在向导里配置了上报、保存为模板后，从模板一键启动的评测不会上报 bad case；用户无法把“hy4-flash + fixed + OpenSpec-cn + SWE-bench 6 用例 + 上报”保存成一个可复用模板。
- 原型同样缺失：M10 原型 Step 3 有上报段，但 `createTemplateFromWizard()` 不复制 `selfEvolution`，模板详情/启动弹层也没有上报行。问题从原型设计就存在，不是生产实现单独引入。

生产证据：模板 `49a201b3-87f6-41a8-a89f-cf124c2bef81`（hy4-flash / fixed-linux / OpenSpec-cn / SWE-bench 6 用例 / 120 分钟）已创建，但详情页没有自进化上报配置；同配置历史 run `5f5d2f48-4ef1-4bb4-af93-453d1a2d1fe7` 在 run 级上报下成功推送 3 条 bad case（HTTP 201）。对照截图见本 issue 末尾的“证据”一节。

## 目标

- 评测模板新增自进化上报配置，与 run 级配置同构：`enabled`、`base_url`、`workspace_id`、`token`；`token` 加密保存，任何响应不回显。
- 发起评测向导“保存为模板”时，把 Step 3 的上报配置一并写入模板；模板详情、一键启动确认与列表摘要展示上报目标（地址/工作区，不展示 token）。
- 模板启动把上报配置传入既有 prepare/create 链路，从模板一键启动的评测在终态按 #266/#341 机制上报 bad case。
- 模板创建/更新接口接受并校验该配置：`enabled=true` 时三字段必填、`base_url` 为 http(s)、`token` 以 `whk_` 开头；`enabled=false` 时三字段必须为空；更新时未提供的字段保持原值，显式停用清空密文。
- 原型先补齐保存、详情、启动确认与列表展示；生产前端与后端按同一契约实现。
- 修改前后截图（生产、原型、原型修改后）随 issue 与 PR 评审。

## 非目标

- 不改变评测执行、调度、评分与轨迹链路；上报仍复用既有 outbox 与退避机制。
- 不在模板启动请求里做字段覆盖；上报配置随模板保存，启动只确认费用与容量。
- 不引入新的模板级上报重试策略；沿用 run 级 60/300/900/3600 秒退避与终态失败语义。
- 本次先交付 issue 描述与原型修改；生产后端/前端实现另行跟进。

## 验收

- 原型：向导启用上报并保存为模板后，模板详情与一键启动弹层显示“自进化上报：已启用 · host · 工作区 ws”，不显示 token；未启用显示“未启用”；列表行有上报状态提示；修改前/修改后截图附在本 issue 与 PR。
- 生产：`eval_template` 持久化上报配置；创建/更新/启动接口按上述规则校验与透传；从模板启动的 run 在终态产生 `self_evolution_report` 并成功 POST（200/201），重复触发幂等。
- 生产前端：模板详情/启动确认显示上报目标；向导保存模板时保留 Step 3 配置；令牌输入掩码且不进入浏览器持久化草稿。
- 测试：模板创建/更新校验、启停清空、启动透传、令牌不回显、既有模板默认未启用均有测试；CI 全绿。
- 文档：架构/设计文档与原型 README 同步模板级上报契约；截图证据同视口、同主题。

## 参考

- PR #266（自进化上报）、PR #341（会话寻址）。
- Issue #378（评测模板）。
- 生产模板 `49a201b3-87f6-41a8-a89f-cf124c2bef81`；同配置历史 run `5f5d2f48-4ef1-4bb4-af93-453d1a2d1fe7`。
- 设计：`docs/design/eval-platform-architecture.md`、`plans/m1-02-fullstack-design/`。
- 关联 PR：#383（原型修复；生产实现待跟进）。

## 证据

同视口 1600×1000、浅色主题，均为真实应用/原型截图。

### 生产（当前实现）

| 模板详情（无自进化上报） | 一键启动（固定配置无上报） | 发起评测 Step 3（有上报配置） |
|---|---|---|
| ![生产模板详情](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/prod-template-detail.png) | ![生产一键启动](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/prod-template-launch.png) | ![生产发起评测 Step 3](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/prod-wizard-step3-reporting.png) |

结论：生产模板能保存数据集/资产/模型/资源池/执行策略，但详情与一键启动都没有自进化上报；同一套上报配置在发起评测 Step 3 又存在，说明能力在 run 级可用、模板级缺失。

### 原型修改前

| 模板详情（无自进化上报） | 一键启动（固定配置无上报） | Step 4（有上报配置） |
|---|---|---|
| ![原型修改前模板详情](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-before-template-detail.png) | ![原型修改前一键启动](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-before-template-launch.png) | ![原型修改前 Step 4](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-before-wizard-step4.png) |

结论：原型与生产同样缺失；问题从原型设计就存在，不是生产实现单独引入。

### 原型修改后

| 模板详情（显示上报目标） | 一键启动（显示上报目标） | Step 4（保存模板包含上报） |
|---|---|---|
| ![原型修改后模板详情](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-after-template-detail.png) | ![原型修改后一键启动](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-after-template-launch.png) | ![原型修改后 Step 4](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-after-wizard-step4.png) |

修改后模板保存、详情、一键启动与列表状态都包含自进化上报目标；令牌只显示“加密保存，不回显”。
