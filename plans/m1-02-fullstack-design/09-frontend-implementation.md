# 09 · 前端实现设计

## 1. 技术组织

建议 `apps/web` 使用 React + TypeScript + Vite + React Router + Ant Design + TanStack Query；OpenAPI 生成 TypeScript 类型与请求 DTO。MVP 不强制引入全局状态库，只有跨页面向导草稿或复杂 trace view 确有需要时再增加轻量 store。

```text
apps/web/src/
  app/                 router、providers、权限与主题
  api/generated/       OpenAPI 生成物
  api/                 按域薄封装、SSE、错误归一化
  components/          通用状态、表格、确认框、拷贝、时间
  features/
    auth/
    datasets/
    assets/
    models/
    resources/
    evaluations/
    traces/
    deployments/
  pages/               路由组合，不放复杂业务算法
  styles/tokens.css    原型令牌与 AntD 映射
  test/                fixtures、MSW、渲染工具
```

## 2. 设计令牌与 Ant Design 分工

```text
colorPrimary      #1677ff
colorText         #1f2937
colorTextSecondary#4b5563
colorBorder       #e7ecf3
colorBgLayout     #f2f5fa
borderRadius      10px
fontSize          14px
```

- 侧栏、卡片、指标、容量、状态进度、轨迹三栏等业务视觉自研。
- AntD Table 可作为数据集/用例表格底座，但堆叠状态、行内轨迹链接和批量操作按原型封装。
- 标签色语义固定：success=resolved/健康，warning=partial/需关注，error=failed/timeout，processing=running，default=queued/unresolved。
- 所有异步按钮有 loading；危险按钮不能用主色掩盖风险。

## 3. 路由与深链

```text
/login
/overview
/datasets
/datasets/import
/datasets/:datasetId
/models
/assets
/resources
/evals
/evals/new
/evals/:runId?tab=overview|cases|results|config|events
/evals/:runId/cases/:caseRunId/trace?attempt=1&session=...&span=...
/deployments
```

未登录记录 `return_to`，登录后只允许站内相对路径，防开放重定向。页面级权限在路由和 API 双层检查；无权限页面提供明确返回路径。

## 4. 数据层

### 4.1 Query Key

```text
['datasets', filters]
['dataset', id]
['dataset-version', versionId, 'cases', filters]
['eval-runs', filters]
['eval-run', runId]
['eval-run', runId, 'cases', filters]
['eval-run', runId, 'results']
['eval-case', caseRunId, 'trace', attempt]
['trace-session', sessionId, 'spans', filters]
['resource-pool', poolId, 'capacity']
```

筛选和分页进入 key；旧请求取消，响应不能跨 run/attempt 合并。Mutation 成功后只失效相关 key，不全站 refetch。

### 4.2 SSE Hook

`useEvalEvents(runId)` 流程：读取详情快照 cursor → 建连 → 按事件类型精确失效/合并 → 记录 lastEventId → 断线指数退避 → gap 时重取快照。组件卸载关闭连接；终态停止重连；隐藏标签页可降低刷新频率但不丢事件。

实时数字不直接在多个组件各自累加。进度汇总来自同一 run query，case 行更新后由服务端下发/重取汇总，避免前后端计数漂移。

## 5. 页面组件边界

### 5.1 发起评测

```text
EvalLaunchPage
  ├─ LaunchStepper
  ├─ DatasetStep
  ├─ AgentAssetsStep
  │   ├─ RuntimeSelect
  │   ├─ AssetSourceSelect
  │   ├─ RefResolver
  │   └─ AssetTreeSelection
  ├─ ModelResourceStep
  │   ├─ ExistingOrTemporaryModel
  │   ├─ ResourcePoolCapacity
  │   └─ ExecutionPolicyFields
  ├─ LaunchSummary
  └─ LaunchFooter
```

向导只保存非敏感草稿；临时 API Key 不进入 URL、localStorage、日志或 Query cache 持久化。离开页面时提示未提交配置；Prepare 后修改任一项使旧 preparation 失效。

### 5.2 评测详情

```text
EvalDetailPage
  ├─ EvalStatusHeader
  ├─ StackedCaseProgress
  ├─ EvalMetricCards(isFinal, denominatorPolicy)
  ├─ BaselineComparison(auto recommendation + manual selector)
  ├─ BudgetPauseBanner(spent + remaining cases + resume action)
  ├─ EvalTabs
  │   ├─ LiveCaseSample
  │   ├─ CaseTable(filters + pagination + attempt actions)
  │   ├─ TestResultTable(F2P/P2P + outcome + failure log)
  │   ├─ ResultAnalysis(definition-driven cards/charts)
  │   ├─ ImmutableConfigSnapshot
  │   └─ EvalEventTimeline(cursor pagination)
  └─ CancelEvalDialog
```

图表读取 metric definition 的 `display_kind/config`，但只支持前端注册的渲染器；未知渲染器回退为数值/表格，不执行动态组件代码。

### 5.3 会话轨迹

```text
TracePage
  ├─ AttemptSelector
  ├─ TraceIngestionBanner
  ├─ SessionTree
  ├─ TraceWaterfall
  │   ├─ SpanTypeFilter
  │   ├─ TimeAxis
  │   └─ VirtualizedSpanRows
  ├─ SpanInspector
  │   ├─ SpanSummary
  │   ├─ MessageContent
  │   ├─ AttributeTable
  │   └─ RawRecordDisclosure(permission)
  └─ RawTraceDownload
```

Waterfall 用 CSS/SVG 自研，行虚拟化；不为每个大 span 一次性渲染完整正文。文本默认按纯文本显示，Markdown 若启用必须 sanitize，工具输出中的 HTML/脚本不可执行。

## 6. 状态与错误处理

- 首屏加载用骨架，局部刷新保留旧数据并显示更新时间；无数据不显示无限骨架。
- 401 清理会话并回登录；403 展示权限说明；409 展示状态已变化并刷新；422 映射到表单字段；5xx 提供 request ID 和重试。
- 取消/重试/回滚确认框显示影响范围；等待 Mutation 时禁用重复提交，但最终以服务端幂等为准。
- Toast 只承载短反馈；导入部分失败、轨迹降级、容量不足等重要状态必须留在页面。
- 凭据错误不回显用户输入的 Key；前端监控和错误上报排除表单敏感字段与轨迹正文。

## 7. 可访问性与桌面适配

- 所有图标按钮有名称，状态不只依赖颜色，进度条有文本等价物。
- Modal/Drawer 焦点锁定与返回触发元素，会话树支持键盘上下/展开，span 行可聚焦。
- 1280×800 下轨迹三栏允许右侧抽屉化，主操作保持可见；MVP 不承诺手机端完整 waterfall。
- 大列表使用服务端分页或虚拟化，不为“动画效果”阻塞交互。

## 8. 前端测试

| 层 | 重点 |
|---|---|
| 组件 | 状态标签、堆叠进度、指标口径、容量卡、权限锁定 |
| 向导 | Ref 失效、资产跨页全选、临时 Key 不持久化、Prepare/Create 幂等交互 |
| 用例表 | 筛选/搜索/分页 URL、部分成功、旧 attempt 深链、测试级状态与完整失败日志 |
| SSE | 重复、乱序、gap、重连、终态、旧 run 事件隔离 |
| Trace | 主/子树联动、unlinked、degraded、raw 权限、大内容懒加载 |
| E2E | 登录→导入→配置→发起→进展→结果→用例→轨迹连续主路径 |
| 视觉 | 关键页面 1440×900 与 1280×800 截图对比原型 |
