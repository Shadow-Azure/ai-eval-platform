# 会话轨迹查看器重设计原型

面向【评测 / 会话轨迹】页的轨迹查看体验重设计。原型是单文件静态 HTML，基于生产环境的一条真实轨迹（`astropy__astropy-12907`，76 spans，5 轮）制作，用于确认交互方向，尚未进入前端开发。

## 打开方式

直接打开 `index.html`；无外部依赖、无网络请求。

截图位于 `screenshots/`：

| 文件 | 内容 |
|---|---|
| `01-current-default.png` | 生产环境默认三栏视图（账号信息已脱敏） |
| `02-current-tool.png` | 生产环境选中工具 span 后的属性表 |
| `03-prototype-conversation.png` | 新原型：对话轨迹 |
| `04-prototype-timeline.png` | 新原型：轮次时间线 |
| `05-prototype-detail-input.png` | 新原型：右栏「输入」查看工具参数 |
| `06-prototype-detail-raw.png` | 新原型：右栏「原始」查看 span JSON |
| `07-tool-cards.png` | 新原型：6 种工具步骤展开后的组件示例 |
| `08-step-list.png` | 新原型：步骤视图，默认全部折叠 |
| `09-step-edit-open.png` | 新原型：展开 Edit，直接看 diff |
| `10-step-subagent-open.png` | 新原型：展开 Subagent 示例，看嵌套步骤 |

`reference-shots/` 是 LangSmith、Braintrust、Langfuse、Arize Phoenix、PostHog 官方文档中的界面截图；`reference-shots/highstar/` 是 OpenCode、Claude Code UI、claude-code-log 等高星项目的官方界面截图。均仅作交互参考。

## 设计的出发点

生产页已有“会话树 + waterfall + span 属性”三栏骨架，但对这条 76 span 的真实轨迹，主要问题是：

1. **看不到 agent 在做什么。** 中栏是 76 行扁平 span，只有 `agent / llm / thinking / tool / message` 五种英文类型；需要用户在脑中把 `LLM → thinking → Bash → tool_result` 还原成一次操作。
2. **时间轴不可读。** 76 个 span 中 75 个起止时间戳相同，`duration_ms` 为空，因此所有行显示 `0 ms`，瀑布条退化成零宽标记；唯一可读的是根 span 的 1m 9s。
3. **重复噪声。** 每次工具调用同时产生 `MESSAGE user` 与 `TOOL tool_result` 两行，内容重复，却各占一行。
4. **详情不可读。** 右栏默认展示 `gen_ai.*` / `openinference.*` 语义属性表，工具参数和输出仍是 JSON/原始文本，排查时需要自己解析。
5. **缺少定位手段。** 只有按 kind 过滤和“加载更多”，没有搜索、错误聚焦、轮次导航或关键路径折叠。

## 结构

```
┌ 轨迹概览：5 轮 · 76 spans · 按执行顺序的“指纹” ──────────────┐
├ 会话下拉        ┬ 对话轨迹（默认）        ┬ 详情检查器        ┤
│ 主/子 Agent     │ 用户任务               │ Edit               │
│ 01 理解任务     │ 01 理解任务（折叠）     │ 变更摘要 + diff    │
│ 02 定位问题     │ 02 定位问题（折叠）     │ 工具返回           │
│ 03 实施修复     │ 03 实施修复（展开）     │ 执行信息           │
│ 04 回归验证     │ 04 回归验证（折叠）     │ 关联证据           │
│ 05 总结回答     │ 05 修复完成（折叠）     │ 原始 / 输入 / 输出 │
│ 原始文件（折叠）│                        │                    │
└────────────────┴───────────────────────┴───────────────────┘
```

## 关键设计决策

1. **先叙事后瀑布。** 默认视图按“轮次”组织：每轮包含模型回复、思考、工具调用与结果，先回答“agent 做了什么”，再进入时间线做性能定位。参考 LangSmith 的 Trajectory / Turns 双层结构。
2. **轮次是展示层分组。** 不新增后端模型：以 LLM 调用和工具结果为边界，把已有 span 归并为轮次；折叠时只显示摘要与 span 数，展开时仍可追溯到具体 span。
3. **零宽时间用“轮次窗口 + 顺序指纹”表达。** 顶部指纹按执行顺序展示 76 个 span；时间线按每轮首末 span 的时间窗口聚合，明确标注“75/76 个 span 时长为 0 ms”，不伪造耗时。这比“一排 0ms 瀑布”更诚实，也更有信息量。
4. **详情以任务可读性优先。** 右栏对 Edit 展示 diff，对 Bash 展示命令与输出，对 LLM 展示 usage；`属性 / 原始` 仍保留，供深度排查。这是对现有 `ATTRIBUTES / INPUT / OUTPUT / RAW` 的增强而非删除。
5. **去重但不丢证据。** 工具结果与 `tool_result` span 在轮次里合并为一条可展开记录，底层 span 仍可打开；重复的 message 不再占用主视图。
6. **保留三栏，但重新分配权重。** 左栏从“只有一个会话的树”变成“会话下拉 + 轮次导航 + 原始文件”；中栏承担阅读；右栏承担钻取。窄屏时右栏可收起（原型未实现响应式）。
7. **会话切换放在最高频的位置。** 主 Agent / 子 Agent / 未关联会话统一放进左栏顶部的下拉，默认主 Agent；每项显示角色、Agent 名称、原生 sessionId 与 span 数，触发器持续显示当前选中会话的 sessionId。
8. **按阅读频率决定默认展开。** 关键节点默认展开，原始文件默认折叠；两者保留标题、计数与摘要，点击标题即可切换，避免低频操作长期占用左栏。

## 参考的同类产品

| 产品 | 可借鉴的交互 | 本原型的取舍 |
|---|---|---|
| [LangSmith – View traces](https://docs.langchain.com/langsmith/view-traces) | Trajectory（对话层）/ Turns（轮次摘要）/ Details（运行详情）三视图；thought 默认折叠；并行工具调用合并；每轮展示 token、成本、模型 | 采用“对话 + 轮次 + 详情”的分层；原型把 Turns 与 Trajectory 合并为一栏，减少一次切换 |
| [Braintrust – Examine traces](https://www.braintrust.dev/docs/observe/examine-traces) | Spans 层级 / Timeline（执行流与 token 效率）/ Thread（对话）；双栏 Views + trace；详情按 Details/Review 分 tab | 采用“层级 + 时间线 + 对话”三种视角，但把时间线做成轮次级，适配零宽 span |
| [Langfuse – Agent Graphs](https://langfuse.com/docs/observability/features/agent-graphs) | Tree / Timeline / Graph 并列；Aggregated 与 Expanded 切换，重复步骤聚合计数 | 采用“聚合优先、可展开”的思路；未引入图视图，避免对单 Agent 轨迹过度设计 |
| [Arize Phoenix – Tracing](https://arize.com/docs/phoenix/tracing/llm-traces) | 以 project / session 为入口，span 树 + 详情；OpenInference 语义原生 | 保留按 session 聚合与 OpenInference 属性，仅改变默认呈现 |
| [PostHog – AI traces](https://posthog.com/docs/ai-observability/traces) | 以 trace 为单位的时间线列表，突出每次 generation 的输入输出 | 采用“时间线 + 输入输出”的可读呈现；不引入其产品分析视角 |

共同趋势：**头部产品都不再把“瀑布图”当作唯一入口，而是先给对话/轮次叙事，再提供时间线做性能分析，最后用详情面板承接原始证据。** 本原型沿用这一信息架构。

## 与生产数据的对应

- 用例：`astropy__astropy-12907`，SWE-bench Verified，Attempt 1，已解决，15 passed。
- 轨迹：1 个主 Agent session，76 spans，56,536 input tokens / 8,106 output tokens，成本 ¥0.258。
- 轮次边界来自真实 span 顺序；时间线位置来自真实 `started_at`，缺失的 `duration_ms` 不做猜测。
- 原型中的提示、工具命令、diff 与最终结论均取自该次真实轨迹的脱敏预览。

## 步骤视图

顶部 `步骤` tab 把一次轨迹展开成“按轮次分组的工具步骤”：

- 默认全部折叠，只保留工具名、目标、状态、耗时；不会先看到 JSON。
- 点开 Bash：命令 + 输出 + 退出码；点开 Edit / Write：diff；点开 Read：文件路径 + 内容预览。
- Skill / Subagent 作为嵌套步骤展示：子 Agent 有独立的会话、模型、token 与子步骤，可“打开子会话 / 返回主轨迹”。
- 生产轨迹只有 Bash / Read / Edit；Skill、Subagent、Write 在原型中标注为“设计示例”。
- `tool-details.html` 是 6 种工具展开态的组件示例，用于确认每种步骤的字段与信息层级。

## 细节查看

对话轨迹是入口，不是全部。任意轮次 / 工具调用都能在右栏继续下钻：

1. **概览**：人可读的变更摘要、diff、工具返回与关联证据。
2. **输入 / 输出**：工具参数与结果的原始结构；长字符串可展开。
3. **属性**：`gen_ai.*` / `openinference.*` 语义属性，保留给高级排查。
4. **原始**：完整 span JSON（长文本按 8 KB 截断）。
5. **再往下**：`打开底层 span` 打开完整内容；顶部 `原始会话文件` 仍是受控下载入口；每个 span 可生成 `?span=` 深链。

从摘要素描到原始 JSON 最多 2 次点击，默认视图降低阅读成本，但不删除任何一层证据。

## 参考产品界面

`reference-shots/` 中的截图来自对应产品的官方文档，只作交互参考：

| 文件 | 产品 | 来源 |
|---|---|---|
| `ref-01-langsmith.jpg` | LangSmith | [View traces](https://docs.langchain.com/langsmith/view-traces) |
| `ref-02-braintrust.jpg` | Braintrust | [Examine traces](https://www.braintrust.dev/docs/observe/examine-traces) |
| `ref-03-langfuse.jpg` | Langfuse | [LLM Observability](https://langfuse.com/docs/observability/overview) |
| `ref-04-phoenix.jpg` | Arize Phoenix | [Tracing](https://arize.com/docs/phoenix/tracing/llm-traces) |
| `ref-05-posthog.jpg` | PostHog | [AI traces](https://posthog.com/docs/ai-observability/traces) |

### Claude Code / Codex 高星项目

| 文件 | 项目 | Stars | 来源 |
|---|---|---|---|
| `highstar/ref-08-opencode.jpg` | OpenCode | 226k | [anomalyco/opencode](https://github.com/anomalyco/opencode) |
| `highstar/ref-06-claude-code-ui.jpg` | Claude Code UI | 14.6k | [siteboon/claudecodeui](https://github.com/siteboon/claudecodeui) |
| `highstar/ref-07-claude-code-log.png` | claude-code-log | 1.26k | [daaain/claude-code-log](https://github.com/daaain/claude-code-log) |

另：Crystal（3.1k★，同时支持 Claude Code / Codex 会话，官方截图暂不可用）与 claude-code-viewer（1.3k★）可作为补充参考。

## 交互

- `对话轨迹 / 步骤 / 时间线` 顶部切换；步骤默认折叠，点击任意步骤展开；点击轮次头展开/折叠；点击左侧关键节点滚动到对应轮次。
- 左栏「会话」为下拉：主 Agent / 子 Agent / 未关联，默认主 Agent；选中后触发器显示对应原生 sessionId，下拉收起。
- 「关键节点」默认展开，「原始文件」默认折叠；点击标题均可切换，并更新 `aria-expanded`。
- 右栏 `概览 / 输入 / 输出 / 属性 / 原始` tab 可切换，截图分别展示概览、输入与原始。
- 类型筛选、搜索框、跨 span 联动与“自动展开关键步骤”为视觉占位，未接真实逻辑。
- 时间线的 `按轮次 / 按 span` 为视觉占位。

## 未覆盖 / 待确认

- 未实现窄屏与移动端布局；当前按 1440×900 设计。
- 未接入真实 API；轮次归并规则需要在开发前与现有 trace 数据模型对齐。
- 会话下拉切换目前只更新会话标识，未重渲中间轨迹与右栏；生产实现已按真实 span 重取。
- 子 Agent 分支、轨迹对比、错误聚焦、搜索命中定位尚未制作交互。
- 需要确认：是否把“轮次”作为前后端共享概念，还是仅前端派生。
