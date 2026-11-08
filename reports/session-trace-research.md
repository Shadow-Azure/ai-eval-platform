# AI Agent 会话轨迹（session trace）的存储、规范与跨 Agent 归一化 —— 开源方案调研

> 结论优先；「✅验证」= 本次已核对来源，「⚠️未验证」= 基于社区资料、未经一手确认。

## 结论（TL;DR）

1. **不要自己发明规范**：统一模型直接采用「OpenInference 属性 + OTel GenAI 操作名」的混合语义（二者是同一生态，Phoenix/Langfuse 都消费），自有扩展放 `eval.*` 前缀。
2. **推荐路线：归一化入库 + 原样 JSONL 归档并存**。入库用统一 span 模型（支撑前端树形展示与评测聚合），原始轨迹按 agent 原格式 blob 存对象存储（支撑回放、审计、重评分）。
3. 现成的多 agent 轨迹归一化开源项目已存在（txcript、toolpath、ccsession），可直接复用其解析器或字段映射，避免重造。
4. Langfuse 是「拿来做可观测 UI」的最强选项（MIT、自托管成熟、OTLP 入口）；但其数据模型不是通用 exchange 格式，不建议把自家 DB schema 绑定它。

## 1. OTel GenAI 语义约定（semconv gen_ai）✅

GenAI semconv 已迁往独立仓库 [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)（原 semconv 仓标记 moved），整体状态 **Development**。

- **`gen_ai.conversation.id`**：会话/线程标识，Conditionally Required——仅当框架本来就有会话 id 时才记录，禁止用 UUID/trace id 兜底伪造。来源：[gen-ai-agent-spans.md](https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md)。
- **`gen_ai.operation.name`** 预定义值：`chat`（模型调用）、`invoke_agent`（LLM 驱动控制流的 agent 运行）、`invoke_workflow`、`plan`、`execute_tool`、`create_agent`、`embeddings` 等。span 名应为 `{operation.name} {target}`（如 `invoke_agent Researcher`）。
- **span 类型**：`gen_ai.invoke_agent.internal`（SpanKind=INTERNAL，进程内 agent/子 agent 入口；子 agent 通过父子 span 嵌套表达，并关联 `gen_ai.main_agent` 实体）、`execute_tool`（SpanKind=INTERNAL，`gen_ai.tool.name/call.id`）、`chat`（SpanKind=CLIENT）。
- **属性**：`gen_ai.provider.name`（openai/anthropic/…）、`gen_ai.request.model`、`gen_ai.usage.input_tokens/output_tokens`（含 cache 细分）、`gen_ai.response.finish_reasons`、`gen_ai.agent.name/id`、`gen_ai.input.messages/output.messages`（AnyValue，Opt-In 敏感内容）；消息/工具定义过大时用 `*_ref` 指向外部存储。还有 `gen_ai.invoke_agent.exception` 事件。
- **Langfuse**：v3+ 构建于 OTel 之上，接收 OTLP（`/api/public/otel`，HTTP/JSON 与 protobuf，无 gRPC），会把 `gen_ai.*` 映射到自家数据模型；user/session id 需通过 baggage 传播到每个 span。来源：[Langfuse OTel 集成](https://langfuse.com/integrations/native/opentelemetry)。
- **Arize Phoenix**：原生 OTel 后端 + OpenInference 发明者，对 OTel GenAI span 是一等公民。来源：[Phoenix docs](https://arize.com/docs/phoenix)。（Phoenix 对 OTel 的支持深度 ✅；具体属性映射矩阵 ⚠️未逐项验证。）

## 2. 开源 LLM 可观测平台对比

| 平台 | 数据模型层级 | 写入 API | 自托管 | License | 社区 | Repo |
|---|---|---|---|---|---|---|
| **Langfuse** | session → trace → observation（span/generation/event 嵌套）+ score | OTel SDK（v3/v4）、Python/TS SDK、`/api/public/otel` OTLP | Docker/K8s/Helm，PG+ClickHouse | **MIT**（v2.43 起） | 最活跃，YC | [langfuse/langfuse](https://github.com/langfuse/langfuse) |
| **Arize Phoenix** | project → trace → span（OpenInference span kinds）+ session.id 聚合 | OTLP（OTel GenAI 原生）、OpenInference SDK | Docker/pip，SQLite/Postgres | **ELv2**（不可作托管服务对外） | 活跃 | [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) |
| **Laminar** | span 树 + 属性（OpenInference 兼容），有 agent debugger | OTLP、Python/TS SDK | Docker Compose / Helm（PG+ClickHouse+Quickwit） | 开源，具体 LICENSE 文件⚠️未验证 | 中小 | [lmnr-ai/lmnr](https://github.com/lmnr-ai/lmnr) |
| **Helicone** | request/response 日志 + 派生指标，弱嵌套 span | 网关代理 / SDK 拦截 | Docker（含 ee 目录） | **Apache-2.0** | 活跃 | [Helicone/helicone](https://github.com/Helicone/helicone) |
| **AgentOps** | session → event 列表（偏时间线而非嵌套树） | Python SDK 装饰器/回调 | 主要是 SaaS，自托管弱 | MIT⚠️（PyPI 元数据，LICENSE 未逐一核对） | 一般 | [AgentOps-AI/agentops](https://github.com/AgentOps-AI/agentops) |

要点：要「嵌套 agent 树 + session 分组 + 自托管 + 宽松 license」，Langfuse 唯一全满足；Phoenix 语义最干净但 ELv2 限制二次分发。

## 3. OpenInference 如何表达「主 agent + subagent」✅

来源：[Arize-ai/openinference spec/semantic_conventions.md](https://github.com/Arize-ai/openinference)。

- **`openinference.span.kind`**（必填）：`AGENT`（encompasses LLM+Tool 调用的推理块）、`LLM`、`TOOL`、`CHAIN`、`RETRIEVER`、`EVALUATOR` 等。
- **父子结构**：纯靠 OTel span 父子关系——主 agent 为 `AGENT` span，subagent 是其子 `AGENT` span（`agent.name` 区分），LLM 调用为 `LLM` span、工具为 `TOOL` span，天然构成树。
- **`session.id`**：保留属性，把多个 trace 归进一个会话；还有 `session.evaluations`/`trace.evaluations`（扁平化索引属性）。
- **token/成本**：`llm.token_count.prompt/completion`（含 `prompt_details.cache_read/write`）、`llm.cost.*`（USD）；消息用 `llm.input_messages.N.message.role/content` 扁平索引。

## 4. 各 agent 原生会话数据形态 ✅（均核对过一手文档）

| Agent | 落盘位置 | 格式 | 元数据 |
|---|---|---|---|
| **Claude Code** | `~/.claude/projects/<project-dir>/<session-id>.jsonl`（可用 `CLAUDE_CONFIG_DIR` 迁移；30 天保留） | JSONL，每行一条消息/工具/请求记录，**格式内部化、随版本变** | 模型名、token 用量、成本、工具调用；`claude -p --output-format json/stream-json` 输出 result/usage/sessionId；Agent SDK 可编程接收每条消息。来源：[sessions 文档](https://code.claude.com/docs/en/sessions)、[headless](https://code.claude.com/docs/en/headless) |
| **OpenAI Codex** | `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`（7 天未动压缩为 .zst；另有 `state_<N>.sqlite` 存 threads 注册表：tokens_used、model、cwd、git 信息） | JSONL 信封 `{timestamp,type,payload}`：`session_meta`/`turn_context`/`response_item`（message/reasoning/function_call…）/`event_msg`（token_count 为**累计**用量） | 模型、累计 token（input/cached/output/reasoning）、工具调用、subagent 经 `source.subagent.thread_spawn.parent_thread_id` 关联父会话。来源：[codex-cli-session-format](https://github.com/rajbos/ai-engineering-fluency/blob/main/docs/logFilesSchema/codex-cli-session-format.md) |
| **OpenCode** | `~/.local/share/opencode/opencode.db`（SQLite WAL，drizzle 迁移），**非 JSONL**；文件快照存在 `snapshot/` 下的裸 git 仓 | 表：`session`（含 parent_id 表 fork/subagent 链）、`message`（user/assistant，含 model、cost、tokens）、`part`（text/reasoning/tool/step-start/step-finish/subtask…，step-finish 带逐步 token 与 cost） | 每步 token（input/output/reasoning/cache）、美元成本、工具调用、subtask part + `session.parent_id` 表达子 agent。来源：[opencode format](https://raw.githubusercontent.com/empathic/toolpath/main/docs/agents/formats/opencode.md) |
| **pi（earendil-works）** | `~/.pi/agent/sessions/--<path>--/<ts>_<session-id>.jsonl` | JSONL，v3 树形结构（id/parentId），entry 类型：session 头、message、usage、model_change、compaction 等 | assistant message 携带 model/provider/usage/stopReason；usage 独立 entry；SDK 可自定义 session id。来源：[session-format](https://pi.dev/docs/latest/session-format) |

## 5. 业界已有的「归一化」现成做法 ✅

- **txcript**（[skillsynchq/txcript](https://github.com/skillsynchq/txcript)）：Rust 写的多编码 agent 会话读取/转换器，定义统一 **Common 模型**（Meta/Message/Thinking/ToolUse/ToolResult/Usage），已有 Codex、Claude Code 等格式解析器，原生层保留 raw JSON 无损往返。与你的需求重合度最高。
- **toolpath**（[empathic/toolpath](https://github.com/empathic/toolpath)）：为 Codex/OpenCode/pi/Gemini 等每种格式写了 schema 文档 + 到统一 ConversationView（Turn/Delegation/ArtifactChange/ConversationEvent）的映射，是现成的字段映射参考书。
- **OpenHands event stream**（[software-agent-sdk](https://docs.openhands.dev/sdk/arch/events.md)）：append-only 类型化事件日志（Message/Action/Observation/SystemPrompt/Condensation…），`to_llm_message()` 双向转换——证明「事件日志 + 按需投影成消息」比直接存消息更抗压缩/回放。
- **inspect_ai**（[UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai)，文档 [eval-logs](https://inspect.aisi.org.uk/eval-logs.html.md)）：eval 维度设计可借鉴——`EvalLog`（header：eval 元信息/results/stats）+ samples（每样本 input/output/score/**events transcript**），`.eval` 二进制格式比 JSON 小 8 倍、消息去重 + zstd 压缩达 10:1，attachments 机制去重大块内容，score 带编辑历史与 provenance。**它的 transcript 模型是「评测事件流」而非通用 agent 树，但「评测↔轨迹」的两层关联（eval_id → sample → session）正是你的前端跳转链路。**
- 另：ccsession、tokenleak、TrajMark（轨迹取证，[arXiv](https://arxiv.org/html/2609.10416v1)）均验证「读各家本地会话文件」路线可行。

## 6. 建议：统一会话轨迹数据模型（草案）

**路线取舍**：归一化 vs 原样存储。前者查询/展示/评分统一但会丢各家独有字段且解析器要跟版本；后者保真但要为每个 agent 写展示适配。→ **评测平台选「归一化入库为主 + 原始 blob 归档为辅」**：评测聚合和树形 UI 只吃统一模型，重放/审计回原始 JSONL，适配器成本由 txcript/toolpath 现成资料大幅降低。

表结构（PostgreSQL）：

```sql
eval_run(id, agent_name, agent_version, model, started_at, status, …)
eval_case_run(id, eval_run_id, case_id, result, score, session_id)  -- 跳转枢纽
session(id, agent, agent_session_id, parent_session_id NULL,  -- 主/子 agent 树
        eval_run_id, eval_case_run_id, cwd, model, started_at, ended_at,
        total_input_tokens, total_output_tokens, total_cost_usd,
        raw_uri, raw_format,          -- 原始 JSONL/SQLite 归档位置
        attrs jsonb)                  -- session.id / eval.* / 其余扩展
span(id, session_id, parent_span_id, trace_id,
      kind,                          -- AGENT|LLM|TOOL|MESSAGE|EVENT
      name,                          -- gen_ai.operation.name 语义
      started_at, ended_at, status,
      model, input_tokens, output_tokens, cost_usd,
      input jsonb, output jsonb,     -- 消息/工具参数（截断策略同 semconv *_ref）
      attrs jsonb)                   -- gen_ai.*/openinference.* 兼容键
event(id, span_id NULL, session_id, ts, type, payload jsonb)  -- 压缩/重试/评分等
```

- 层级语义对齐 OpenInference：`kind=AGENT` 嵌套表达 subagent 树（span 父子），与 OpenCode `session.parent_id`、Codex `parent_thread_id` 两种「会话级父子」都映射进来。
- 属性键直接沿用 `gen_ai.operation.name`、`gen_ai.usage.*`、`session.id`；评测域用 `eval.run.id`/`eval.case.id` 自定义键，便于未来接 OTLP。
- 参考 inspect_ai：原始轨迹存对象存储 + 消息去重/压缩；score 独立表带 provenance。
