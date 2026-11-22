# [m10] 评测模板：固化默认配置、一键启动与三个执行接口

[English](m10-eval-template-quick-launch.en.md) | 中文

```yaml flow
kind: issue
milestone: m10
priority: P1
status: ready
scope:
  - backend/app/**
  - backend/migrations/**
  - tests/**
  - web/src/**
  - prototype/**
  - docs/**
  - plans/**
  - .agents/flow/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 378
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/378
```

## 背景

当前发起一次评测要经过四步向导，每次重新选择数据集版本、Agent 运行时与 Git 资产、模型凭据和资源池；这些组合在真实使用中高度重复，却只能靠记忆复现。eval_run 会冻结一次执行的配置，但没有可复用的任务定义，也没有把触发、状态与结果收敛成稳定接口，外部脚本无法按 ID 直接启动一次固定组合的评测。

## 目标

- 新增项目级 eval_template：固化数据集版本、Agent 运行时、Agent 资产源/Ref/资产选择、模型凭据、资源池、用例范围、执行策略、附加指令、最大轮次与 Agent 超时；模板可启用、停用和编辑。
- 评测任务页新增快速启动模板区：每张卡展示固定组合、最近一次运行和启动按钮；点击一键启动只需确认，不再进入四步向导。
- 在发起评测向导第 4 步支持保存为评测模板；模板保存后出现在快速启动区。另提供新建模板入口，保存模板但不立即启动。
- 三个稳定接口：
  - POST /api/v1/eval-templates/{template_id}/launch：按模板触发一次评测，返回 run_id、状态与状态/结果地址；支持幂等键。
  - GET /api/v1/eval-runs/{run_id}/status：轮询执行状态、进度与是否终态。
  - GET /api/v1/eval-runs/{run_id}/results：读取指标快照与 is_final；运行中返回部分结果并明确标记未终态。
- 每次启动都重新解析模板引用的资产 Ref、凭据和资源池，冻结为新的不可变快照；eval_run 记录 template_id、模板版本与启动时快照，模板后续编辑不影响已启动的评测。
- 模板启动不允许覆盖固定字段；引用已删除或停用的数据集、模型、资源池或资产源时拒绝启动并返回明确错误码。
- 同步更新架构设计文档，原型修改前后截图与本 issue 一并评审。

## 非目标

- 不做定时或周期触发、批量不同配置的 matrix 启动或模板参数占位符。
- 不引入独立的机器令牌体系；首期三个接口复用控制台会话鉴权，启动仍需管理员权限。
- 不改变评测执行、调度、评分与轨迹链路；模板只复用既有 prepare/create 流程。
- 本期只交付设计、原型与截图确认；生产实现随后按本 issue 验收项落地。

## 验收

- 模板列表展示固定组合、最近启动与状态；一键启动确认后创建评测并跳转详情，详情标明来源模板。
- 模板创建可从发起评测第 4 步保存，也可从新建模板入口只保存不启动；名称为空、资产 Ref 未解析或引用失效时阻止保存或启动。
- 三个接口按上述路径返回稳定字段；status 在终态返回 terminal=true，results 在运行中返回 is_final=false，终态后返回完整指标。
- 幂等键重放返回同一 run_id；停用模板返回 409；不存在的模板或评测返回 404；越权启动返回 403。
- npm test、npm run verify:flow、npm run lint:docs、npm run verify:docs、相关后端测试与前端构建通过；原型截图同视口、同主题并附在本 issue。

## 参考

- 设计文档：`docs/design/eval-platform-architecture.md` 的“评测模板与一键启动”一节。
- 原型：`prototype/index.html` 的评测任务页、发起评测第 4 步与模板详情页。
