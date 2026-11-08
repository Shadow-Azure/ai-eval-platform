# 08 · 会话轨迹归一化

## 1. 目标与边界

目标是从任意 `eval_case_attempt` 还原主 Agent、subagent、模型调用、工具调用、消息与 thinking 的可导航轨迹，同时保留原生文件供审计。统一模型对齐 OpenTelemetry GenAI 与 OpenInference 的属性语义，但不假装不同 Runtime 的信息量完全一致。

核心原则：

- **原件是权威**：流式输出用于实时预览，最终原生文件用于完整归一化。
- **证据优先**：只有原生 parent/session/thread/tool-call 关联才能建树；时间相邻不等于父子。
- **版本化解析**：Runtime 版本、原生格式版本、parser_version 全部入库。
- **大内容外置**：DB 存元数据、顺序、摘要和 OSS 引用，大正文按需读取。
- **可降级**：解析失败或关系不明时保留可浏览 raw，不阻塞评分结果。

## 2. 官方资料与可复用机制

以下来源于 2026-11-08 在线核对；实现时仍需锁定具体 Runtime 版本并维护 fixtures，不能把 main 分支文档当作所有版本的兼容承诺。

| Runtime/标准 | 可利用能力 | 设计启示 | 来源 |
|---|---|---|---|
| Claude Code | 非交互 `claude -p`、JSON/stream-json 输出、最终用量与成本 | 用结构化流而非解析终端文本；资产加载需隔离 HOME | [Headless mode](https://code.claude.com/docs/en/headless) |
| Codex | `codex exec --json`；开源事件处理器包含 thread/item/turn、token 和 CollabAgentToolCall | 用 thread ID 与 sender/receiver thread 证据关联协作 Agent | [exec 文档入口](https://github.com/openai/codex/blob/main/docs/exec.md)、[JSONL 事件实现](https://github.com/openai/codex/blob/main/codex-rs/exec/src/event_processor_with_jsonl_output.rs) |
| OpenCode | CLI/Server API；Server `/event` SSE 首事件后持续推送 bus events；`/doc` 提供 OpenAPI 3.1 | 优先生成/固定 SDK 契约，事件用于实时，最终存储用于补齐 | [CLI](https://opencode.ai/docs/cli/)、[Server](https://opencode.ai/docs/server/) |
| pi | `pi --mode json` 严格 JSONL；最终 message/tool end 是权威；会话文件是带 id/parentId 的树 | delta 只暂存；条目树/分支父 ID 不能直接当 subagent 父会话 | [JSON events](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md)、[Session format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md) |
| OpenTelemetry GenAI | Agent/LLM/tool 调用语义、conversation、usage 等属性约定 | 属性命名对齐，不把 DB span 当作完整 OTel Collector 替代品 | [GenAI spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/) |
| OpenInference | AGENT/LLM/TOOL 等 span kind，graph node parent 关联 | 用统一 kind 和显式 graph parent 支持前端树图 | [Semantic conventions](https://arize-ai.github.io/openinference/spec/semantic_conventions.html) |

除公开资料外，2026-11-08 对当前环境的 Claude Code 2.1.278、Codex 0.166.0-alpha.6、OpenCode 1.17.11 做了只读结构检查；只分析字段、表关系和 ID 匹配，不采集消息正文。观察结果用于确定适配器证据优先级，不替代固定版本的自动化 fixture。

## 3. 两层统一模型

### 3.1 Session

一个 session 表示一个可独立寻址的 Agent 执行上下文：

```text
id / eval_case_attempt_id / runtime_key / native_session_id
root_session_id / parent_session_id / agent_id / agent_name
status / started_at / ended_at / metadata_json / relation_evidence_json
```

主 session 的 `parent_session_id=null`。子 session 必须保存 `relation_evidence_json`，例如：

```json
{
  "type": "codex.collab_agent_tool_call",
  "parent_native_session_id": "thread_parent",
  "tool_call_id": "item_12",
  "receiver_thread_id": "thread_child",
  "confidence": "authoritative"
}
```

`confidence` 只允许 `authoritative` 或 `derived`；`derived` 规则必须版本化且 UI 标记。无法关联的子 session 进入 root 下的 `unlinked` 分组，而不是随机挂到最近 Agent。

### 3.2 Span

```text
session_id / parent_span_id / native_event_id / ordinal
kind: AGENT | LLM | TOOL | MESSAGE | THINKING | CHAIN
name / status / start/end/duration
attributes_json / usage / cost / content_preview / content_object_key
```

建议属性：

| 统一字段 | 示例 |
|---|---|
| `gen_ai.operation.name` | `invoke_agent`、`chat`、`execute_tool` |
| `gen_ai.conversation.id` | 原生 root/session 标识 |
| `gen_ai.agent.name/id` | Runtime Agent 名称与 ID |
| `gen_ai.request.model` | 冻结模型名 |
| `gen_ai.usage.input_tokens/output_tokens` | 最终用量，非 delta 累加 |
| `gen_ai.tool.name` / `gen_ai.tool.call.id` | 工具关联 |
| `openinference.span.kind` | AGENT/LLM/TOOL/CHAIN |
| `eval.run.id` / `eval.case_run.id` / `eval.attempt.id` | 评测归属 |
| `eval.runtime.version` / `eval.parser.version` | 复现与重放 |

属性值按 OTel 标量/数组规范存储；复杂对象放 JSONB 扩展或 OSS，不把整个原生记录无差别复制进每个 span。

## 4. Runtime 适配策略

### 4.1 Claude Code

本机 Claude Code 2.1.278 的实际会话样本已观察到完整的结构化关联链：

```text
主 JSONL 的 Agent tool_use.id
  ↔ 子 Agent .meta.json 的 toolUseId
  ↔ 主 JSONL toolUseResult.agentId / agent_id
  ↔ subagents/agent-{agentId}.jsonl
  ↔ 子 JSONL 每行 agentId + 主 sessionId + isSidechain=true
```

`.meta.json` 还包含 `agentType`、`spawnDepth`、`model` 等元数据。抽样中同时存在 `agentId` 和 `agent_id` 等版本/形态差异，且并非所有历史 meta 都能匹配当前主文件中的 Agent 调用，因此解析器必须按版本兼容，并把“工具调用证据”和“agentId 证据”分别记录；匹配不到的部分进入 degraded/unlinked，不强行关联。

- 执行器使用 headless 结构化输出，持续消费 stdout，stderr 单独作为诊断日志。
- 主/子文件按上述 ID 链归并；`parentUuid` 只用于消息树，不用于 subagent 父会话。
- 子 Agent 的 `sessionId` 可以仍等于主会话，不能仅根据 sessionId 判断它是主 Agent。
- 用量优先最终结果，模型网关汇总用于对账；避免把 streaming usage 快照当增量相加。
- 不使用会跳过评测资产的 `--bare`，改用隔离环境控制 hooks/plugins/auto memory。

### 4.2 Codex

本机 Codex 0.166.0-alpha.6 的 `state_5.sqlite` 已存在专门关系表：

```text
threads(id, rollout_path, agent_nickname, agent_role, ...)
thread_spawn_edges(parent_thread_id, child_thread_id, status)
```

这比从消息文本猜父子更可靠。适配器以 `thread_spawn_edges` 为主关联证据，经 `threads.rollout_path` 找到各子线程 JSONL；抽样关系边的父子线程均能在 threads 表中找到。`attached_thread_parent_id` 表达其他附加关系，不与 spawn edge 混用。

- `codex exec --json` 提供实时 thread/item/turn；保存原始 stdout、rollout 和关系快照。
- 当前开源事件处理器还包含 `CollabAgentToolCall` 的 sender_thread_id、receiver_thread_ids、agents_states，可作为流式关联与状态补充。
- 如果只拿到事件而没有子线程 rollout，保留 spawn span 与“子轨迹缺失”，不凭工具参数虚构子消息。
- JSONL 与状态库冲突时保留差异，按适配器声明的权威源降级。

### 4.3 OpenCode

本机 OpenCode 1.17.11 同时使用 SQLite 与导出的 JSON 存储。SQLite 中：

```text
session(id, parent_id, agent, tokens_*, cost, time_*)
message(id, session_id, time_*, data)
part(id, message_id, session_id, time_*, data)
```

实际子 session 样本的 parent_id 均能关联到已有父 session，agent 样本包含 explore/librarian。适配器以 `session.parent_id` 为父子证据，经 session/message/part 或对应 Server API 取得内容，不能把 message/part 的归属字段误当 Agent 父子。

- 优先使用固定版本 Server OpenAPI/SDK 与 `/event` SSE；原始导出包含 SQLite 必要表或 API 响应清单。
- SSE 断线按 session ID 拉最终状态；无全局游标时不承诺无缝事件回放。
- 数据库读取使用只读快照/备份，避免 WAL 中未合并数据或运行中写锁造成不一致。

### 4.4 pi

当前环境没有 pi 会话样本，以下仅依据官方开源格式，实施前必须用目标版本补真实 fixture：

- `pi --mode json` 严格按 LF 切分 JSONL，持续消费 stdout。
- `message_update` delta 只构造临时内容，`message_end.message` 替换临时消息；工具以 `toolCallId` 关联 start/update/end。
- 会话条目 `id/parentId` 是消息/分支树，不映射为 session 父子；header `parentSession` 表示 fork/clone 来源，也不能默认当成 subagent。
- 只有扩展事件或运行器包装层提供明确 spawn/agent 关系时才建立子 Agent session，否则保留独立文件并标 unlinked。
- usage 是最新累计快照，取最终值而非逐 delta 求和。

## 5. 导入流水线

```text
completion callback
  → trace_file(pending) + manifest digest
  → OSS head/range 校验 size + sha256
  → detect runtime/version/format
  → parse native records (single file failure isolated)
  → build native IDs + sessions + span links
  → transaction: replace derived tree for (file digest, parser version)
  → content previews / redaction / large content refs
  → trace_file(ready|degraded|failed) + eval_case_run.trace_status
  → eval_event + SSE notification
```

同一文件重复回调不重复导入。内容相同且 parser_version 相同返回已有结果；内容变化生成新 trace_file；parser 升级允许从 OSS 原件重放派生树。一个子文件失败时其余 session 可标 `degraded` 并展示，错误要指明文件、行号和 parser 版本，但不泄露完整敏感行。

## 6. 实时预览与最终校正

实时 span 可以保存在 Redis/临时事件流，以 `(session_id,native_event_id)` upsert；页面标识“实时、未归档”。完成归一化后发送 `trace.ingestion.updated`，前端以 DB 版本替换临时状态。

- 没有稳定 ID 的流式 delta 不逐条写 PostgreSQL。
- 活跃 LLM span 的开始/结束使用单调时钟计算持续时间，wall clock 仅用于展示。
- 最终文件缺失时，实时流可降级归档为 raw JSONL，标记 `partial_capture`，不能显示为完整轨迹。
- Token 和成本采用最终消息/网关汇总，实时数字标“估算”或“最近上报”。

## 7. 前端查询与展示

Trace 首页返回 attempt 列表、session 树、统计与 ingestion 状态；span 按 session 分页/虚拟化加载。三栏联动：

```text
选择 session → waterfall 装载该 session + 子 Agent 锚点
选择 span    → 右侧加载摘要与属性
展开大内容   → 独立请求 OSS/API 分段内容
切换 attempt → 全部 Query 失效，禁止旧 session 数据混入
```

Waterfall 时间原点为该 attempt 最早 start；跨 session 时间戳异常时按序号布局并标记“时钟不可比”。父子 span 缺失 parent 时作为 root fragment 并给出数据质量提示。

## 8. 脱敏与权限

- 默认掩蔽 API Key、Authorization、Cookie、Token、密码、私钥、URL userinfo 和常见云凭据。
- 评测数据集的参考补丁/隐藏测试不能因轨迹工具输出泄露给未授权用户。
- thinking 默认提供组织级开关；建议 MVP 对只读角色隐藏完整 thinking，仅展示“有推理段、时长和 token”。
- 工具原始输出可能包含环境变量和密钥，入库预览前脱敏，raw 下载需要更高权限并写审计。
- 脱敏不是可逆加密；原始 OSS 文件使用独立前缀、短期下载 URL 和服务端加密，访问仍需平台授权。

## 9. 最低测试矩阵

| 场景 | 断言 |
|---|---|
| 主 Agent 无子 Agent | 一棵 root session，LLM/TOOL 顺序和最终用量正确 |
| 主 + 多 subagent | 父子由原生证据关联，并行 span 不错挂 |
| fork/resume/分支 | 不误判为 subagent |
| delta 重连/重复 | 最终消息不重复，usage 不累加 |
| 子文件缺失 | degraded + raw 入口，主轨迹仍可看 |
| 格式版本未知 | failed/degraded，不启动“猜测解析” |
| 同文件重复回调 | session/span 数量不变 |
| parser 升级重放 | 新派生树完整替换，原文件与审计保留 |
| 大工具输出 | DB 只存截断预览，详情按需读取 |
| 恶意轨迹内容 | 前端不执行 HTML/Markdown 脚本，下载权限与脱敏有效 |
