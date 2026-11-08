---
name: walkthrough-change-metadata-gen
description: 生成或更新 walkthrough/changes/*.yaml 变更页元数据（功能叙事、用户操作路径、架构变化、方案设计、图示、代码地图、逐段检视注解）时必须使用。目标是让单人维护者快速读懂 PR 并高效检视。不用于文件页/功能页的日常维护，也不替代 walkthrough 门禁。
---

# Walkthrough 变更页元数据生成

## 唯一目标

读者是一位**单人维护者**：他没写这些代码，要在检视压力下快速判断"这个 PR 做了什么、为什么这样做、改了哪里、哪里必须细读"。你的产出是 `walkthrough/changes/<issue>.yaml`，渲染成检视页后要让他**三分钟建立整体认知**，再按需下钻。

通俗 > 严谨。术语第一次出现时用一句话解释。宁可朴素准确，不要华丽含糊。

## 硬性工作流

按顺序执行，不许跳步：

1. **采集输入**（缺一不可，缺了就先补齐再动笔）：
   - `.agents/flow/issues/<slug>.md`：背景、目标、验收
   - 该 issue `adr` 指向的 Agent Note：决策理由与被否方案
   - 关联 plan（如有）：任务分解与验收
   - 真实 diff（`git diff --numstat <base>...HEAD` 与逐文件 diff）：改动面与规模
   - 相关的文件页（`walkthrough/files/**`）与功能页（`walkthrough/features/**`）：既有讲解与锚定片段
   - 用户面的真实操作路径：从入口到可见结果，包含错误/边界路径
2. **写叙事层**：按 `references/narrative-guide.md` 写 feature / userFlow / architectural / design 四层。
3. **配流程图**：`architectural` 有实质内容时必填 `architecturalFlowchart`；`design` 非空时按落地映射功能点逐条填 `designFlowcharts`（每个 `#### ` 小节一条，feature 名与小节名双向对账）。每张图的 `explanation` 必填，回答“图表达什么、为什么这么画”。写法见 `references/flowchart-guide.md`。叙事必须与图互锁：正文沿图讲链路并引用图题，`explanation` 与相邻叙事互补，节点术语与正文一字不差。
4. **配图**：跨模块、状态机、并发/流式、多窗口、数据流之一的，**必须**按 `references/diagram-guide.md` 制作 SVG 并正确引用。
5. **图片讲解并行生成**：先列出图片清单，再按本文件《图片讲解的 subagent 分工》为每张图单独派发一个 subagent；主线程负责收集、校验和统一写入 yaml。
6. **写代码地图**：按 `references/code-map-guide.md` 分诊 attention、写分组 why、逐段注解。
7. **补检视指引与验证**：reviewGuide（order/risks/acceptance）与 verification 如实填写，证据必须真实可跑。
8. **自检**：跑本文件末尾的自检清单与验证命令，全过才算完成。

## 更新已有变更页（增量对账）

yaml 已存在时（字段 `skill_gen_commit_id` 记录上次对齐的最新 commit），**不要从零重写**，按增量流程：

1. `git log --oneline <skill_gen_commit_id>..HEAD` 列出其间提交；`git diff --name-only <id>..HEAD -- ':(exclude)walkthrough/changes/**'` 列出实质变更文件。
2. 逐文件判断影响：叙事层（feature/userFlow/architectural/design）是否仍成立 → 修订；新触碰的源码文件 → 补进 codeMap 与逐段注解；既有注解锚定失配 → 修 snippet/note。
3. 把 `skill_gen_commit_id` 更新为当前 HEAD（短 hash）。**只有 skill 更新流程可以推进该字段**；其余提交不得手改。
4. 容错语义：pre-push 检查允许"id 之后仅触碰 walkthrough/changes/** 的对账提交"——即元数据对账本身不会制造新的落后。

## 图片讲解的 subagent 分工

图片清单包括 `architecturalFlowchart`、每条 `designFlowcharts`，以及所有手写 SVG 引用。**每张图一个独立 subagent**；多张图不得合并给同一个 subagent，主线程也不得静默替代某个 subagent 生成讲解。

1. **先建清单**：登记每张图的 `key`、图题/feature、所属章节、节点与边或 SVG 语义、相邻叙事标题。
2. **独立派发**：每个 subagent 只负责一张图，读取 issue/Note、相关叙事、方案设计、代码地图和真实 diff；它只返回研究结论和讲解草稿，不写文件。
3. **输入契约**：给 subagent 的最小输入包括图片 key、图题、图结构或 SVG 内容、相邻叙事全文、相关文件路径和“术语必须逐字对齐”的要求。
4. **输出契约**：每份返回必须包含 `key`、`explanation`、关键证据或文件路径、不确定点；`explanation` 先给机制结论，再解释主链、分叉、回边或布局为什么这样表达。
5. **主线程收口**：校验每张图恰好有一份返回；统一改写为同一语气后，由主线程一次性写入 yaml，避免并行写冲突。缺一份、key 对不上、术语不一致或证据不支持结论时，要求对应 subagent 重做。

如果环境没有 subagent 能力，停止并说明阻塞；不得把“主线程直接生成”伪装成已完成该流程。

## 质量红线

- **写现状，不写变更史**：页面语义是"这个 PR 是什么"，不是"开发过程中发生了什么"。
- **不复述代码字面**：讲解讲意图、机制、边界；逐行翻译代码是失败产出。
- **不编造**：没查证的行为、没跑过的命令、不存在的图，一律不写。不确定的地方显式写"未验证/边界不明"。
- **诚实标注边界**：讲解由 AI 生成，锚点门禁只保证"代码是真的、讲了、覆盖够"，不保证"讲得对"。发现拿不准的判断，在 risks 里点名请人细读。
- **逐段成对生成**：snippet 与 note 必须在同一个语义判断里成对产出，禁止先列 snippet、再批量套 note，或把两列结果按位置拼接。
- **锚点自包含且唯一**：snippet 必须包含足够上下文，能在当前文件中唯一定位；孤立 `}`、`});`、单行注释或半个函数不得单独成段。重复代码必须扩到能区分同一语义单元。
- **note 逐段唯一**：note 必须点名本段符号、状态转换或边界；同一文件的 changes 不得复用通用说明。把 note 移到相邻段仍成立，说明它没有解释当前段。
- **通用性**：本 skill 与产出都不耦合任何具体 PR 的私有事实；示例一律用通用占位表述。

## 分册路由

- 写四层叙事前，读 `references/narrative-guide.md`（功能/操作路径/架构/方案设计的模板与通俗性标准）。
- 写流程图字段前，读 `references/flowchart-guide.md`（结构化流程图写法、explanation、每功能点一条的配平规则、门禁语义）。
- 需要配图时，读 `references/diagram-guide.md`（SVG 类型选择、制作标准、存放与相对引用规则）。
- 生成图片讲解前，按《图片讲解的 subagent 分工》先列清单，再每图派发一个独立 subagent。
- 写代码地图与逐段注解前，读 `references/code-map-guide.md`（attention 分诊、≤20 行/段、覆盖率口径）。

## 发布前自检

- [ ] 一个不懂这个模块的人，只读功能 + 操作路径 + 方案设计，能复述这个 PR 做了什么、为什么
- [ ] 操作路径每步都是"用户动作 → 系统响应 → 可见结果"，不是功能清单
- [ ] 架构变化区分了"结构变化"与"语义变化"；没有变化就写"无"并说明原因
- [ ] 方案设计包含：约束 → 候选 → 选型理由 → 关键权衡 → 边界
- [ ] architectural 有实质内容时 architecturalFlowchart 非空且形状合法；design 非空时 designFlowcharts 每个落地映射功能点一条且 feature 名与小节名一一对应
- [ ] 每张流程图都有 explanation，读者不读相邻正文也能知道图要表达什么、为什么这样画；说明与正文/代码地图互补，不复述图节点清单
- [ ] 图片清单完整，每张图都有独立 subagent 返回的讲解草稿、证据与 key；主线程完成术语与叙事一致性收口
- [ ] 正文引用了每张流程图的图题并沿图讲链路；图中节点名与正文术语一致，分支边在正文有交代
- [ ] 该配图的场景配了图，图能在浏览器里打开且中文可读
- [ ] must-read 组源码文件逐段注解齐全，每段 ≤20 行，讲"这段在做什么/为什么"
- [ ] 每个 changes 段是自包含语义单元；snippet 在当前文件唯一定位，不含孤立括号或纯注释段
- [ ] 每个 changes 段的 note 唯一、具体，能直接说明该 snippet 的行为；不存在可原样复用到相邻段的通用说明
- [ ] 风险点是真实风险（安全边界/状态机/并发/数据一致性），不是套话
- [ ] 验证命令全部真实跑过

## 验证命令

```sh
node .agents/skills/walkthrough-change-metadata-gen/scripts/audit-code-map.mjs walkthrough/changes/<issue>.yaml
node scripts/verify-walkthrough.mjs --diff <base>   # schema/锚点/覆盖率/对账全绿
node scripts/build-walkthrough.mjs --review <base>  # 检视页可构建
```

任何一条红，先修内容再交付；不许为过门禁降低内容真实性。
