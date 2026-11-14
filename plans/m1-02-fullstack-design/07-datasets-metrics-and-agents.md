# 07 · 数据集、指标与 Agent 适配

## 1. 数据集适配器契约

建议接口：

```python
class DatasetAdapter(Protocol):
    key: str
    adapter_version: str

    def validate_source(self, source: SourceConfig) -> ValidationResult: ...
    def iter_cases(self, source: SourceConfig, options: ImportOptions) -> Iterator[NormalizedCase]: ...
    def build_task(self, case: NormalizedCase, version: DatasetVersion) -> TaskBundle: ...
    def metric_keys(self) -> list[str]: ...
    def score_parser(self) -> ScoreParser: ...
```

导入使用流式/批式写入，不把 500 个或更大数据集全部放内存。每个 case 先通过标准字段与适配器 schema 双重校验，再生成不可变任务包。导入 worker 可重入：同一来源/修订/适配器版本和配置 digest 重复执行时复用或安全重建未完成版本，不重复生成正式版本。

### 1.1 SWE-bench 标准映射

| 上游字段 | 平台字段/任务包 | 说明 |
|---|---|---|
| `instance_id` | `dataset_case.case_key` | 版本内唯一 |
| `repo`、`base_commit` | 标准字段 + `task.toml` | 固定代码基线 |
| `problem_statement` | `instruction.md` | Agent 可见题面 |
| `patch` | OSS reference patch | 仅参考/自检，Agent 不可见 |
| `test_patch` | OSS test patch / 评分准备 | Agent 执行阶段不可读 |
| `FAIL_TO_PASS`、`PASS_TO_PASS` | expected metrics + score config | 评分输入与结果解释 |
| 其他字段 | `extra_json` | 保留但不参与核心关联 |

任务包分“Agent 可见”和“评分专用”两类权限，不能因为同一 OSS manifest 就把参考答案和测试补丁暴露给 Agent。

## 1.1 运行时镜像解析（M8-25）

公开 SWE-bench 数据集不携带 `environment_image`；评测 harness 按 instance 与架构派生镜像名，例如
`swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest`。平台在导入时为每个 case 解析并固化
`dataset_case.environment_image`，允许记录中的 `docker_image` / `image_name` 覆盖；`dataset_version.environment_image`
只作为单镜像数据集的兼容回退。导入校验按 `DATASET_IMAGE_REGISTRIES` 的顺序探测公开 registry；全部源
不可用（404/401/超时）时返回 `dataset_image_unavailable`，在写入任何版本前阻断。提交阶段再次拒绝
`image_check.status=blocked` 的导入，避免校验后源状态变化造成半可用版本。Runner 的 case task 接收
per-case 镜像，版本级镜像仅在 case 未携带镜像时回退。

## 1.2 公开镜像源的默认与覆盖（M8-26）

部署默认源由 GitHub Secrets 的 `DATASET_IMAGE_REGISTRIES` 提供，deploy workflow 经
`deploy/write-managed-env.sh` 写入后端 `.env`，compose 的 api/worker 注入同名变量。管理员可在
`/settings/dataset-images` 调整当前环境的覆盖；覆盖持久化到 `dataset_image_sources_setting.sources_json`
（有序、含 enabled），探测时优先使用覆盖，删除覆盖后回退部署默认。上传 worker 与 legacy 导入都读取
同一生效列表，保存后下一次校验生效，不需要重启。

## 1.3 发起评测的最大轮次（M8-31）

评测向导 Step 3 可选配置 `max_turns`；留空按 200，范围 1–1000。该值随评测快照冻结到
`eval_run.max_turns`，创建/准备 API 校验并透传，shard manifest 携带它，Runner 用它覆盖
`REAL_TASK_MAX_TURNS` 默认值。轮次语义是模型响应（assistant turn / API round-trip），一个 turn
可以包含多个工具调用；它不是工具调用次数，也不是用户消息数。旧评测行没有值时读取为 200。

## 2. 五文件任务包与 OSS 布局

架构固定任务包为 `task.toml`、`instruction.md`、`environment/Dockerfile`、`tests/test.sh`、`solution/solve.sh`。建议 manifest 另存于版本级索引，记录每个文件 digest、评分镜像 digest、可见性和运行时约束。

```text
oss://eval-artifacts/
  datasets/{dataset_id}/{version_id}/
    source-manifest.json
    cases/{case_key}/
      task.toml
      instruction.md
      environment/Dockerfile
      tests/test.sh
      solution/solve.sh
      manifest.json
  evals/{eval_run_id}/
    assets/{snapshot_id}/bundle.tar.zst
    attempts/{attempt_id}/raw/...
    attempts/{attempt_id}/score/...
    attempts/{attempt_id}/artifacts/...
```

`solution/solve.sh` 只在任务包自检中使用；正式评测任务视图应生成不包含 solution 和评分私密文件的 Agent 包。Runner 的评分阶段再使用独立权限读取测试材料。

## 3. 指标与结果扩展

### 3.1 三层模型

```text
MetricDefinition（名称、版本、口径、展示、聚合器）
  → CaseMetricSnapshot（每 attempt 原始指标 + 解析器版本）
  → EvalMetricResult（选中用例集合上的聚合值、分母、是否最终）
```

新增数据集时注册一组指标；新增同族版本只关联既有定义。新增简单指标可组合 `ratio/sum/mean/percentile/histogram` 白名单，复杂评分逻辑新增聚合器实现并随平台版本发布，不能在 DB 中保存并执行任意 Python。

### 3.2 SWE-bench 官方口径与通用指标配置

SWE-bench 官方 `make_run_report` 输出 `total/submitted/completed/resolved/unresolved/error/empty_patch` 等集合和计数。官方文档明确：`resolved + unresolved` 是实际完成评分的数量；`error` 表示没有可用结果，常见原因包括补丁无法应用、容器未启动和超时；基础设施失败分类是附加信息，不从总数中扣除。榜单展示的是解决的任务实例百分比。

因此平台主指标固定为：

```text
% Resolved = resolved_instances / total_instances
```

其中 `total_instances` 是本次选中的全部数据集用例；错误、空补丁、未完成和未解决都不能从分母剔除。运行快照也使用同一分母，只标记 `is_final=false`，不在运行中偷偷换公式。另设 `resolved_rate_scored = resolved / (resolved + unresolved)` 作为辅助诊断指标，但不能取代榜单可比的主指标。

其他数据集通过 MetricDefinition 配置复用机制：

```json
{
  "metric_key": "resolved_rate",
  "aggregator_key": "ratio",
  "numerator": {"case_result": "resolved"},
  "denominator": {"case_set": "selected_total"},
  "display_kind": "percentage",
  "version": 1
}
```

分子和分母只能引用注册的用例结果集合，例如 `selected_total/submitted/graded/resolved/execution_failed`，不能填写任意 SQL。新数据集可选择 `selected_total`（全量）、`graded`（已评分）或 `submitted`（已提交）作为分母，并将选择与公式说明冻结到 eval_run。

| metric_key | 口径 | 展示 |
|---|---|---|
| `resolved_count` | 最终有效 attempt 中 resolved 的逻辑用例数 | 数值卡 |
| `resolved_rate` | resolved / selected_total，对齐 SWE-bench 主口径 | 主结果卡 |
| `resolved_rate_scored` | resolved / graded，无已评分用例时为 N/A | 辅助诊断卡 |
| `execution_failure_rate` | 最终执行失败/超时/取消数 / selected_total | 质量告警 |
| `tokens_per_case` | 所有 attempt token 总投入 / 已执行用例数 | 成本卡 |
| `cost_per_case` | 所有 attempt 成本 / 已执行用例数 | 成本卡 |
| `duration_percentiles` | 逻辑用例最新有效 attempt 的 p50/p95 | 分布图 |

主结果同时展示全量解决率与执行失败率。Token/成本明确区分“所有 attempt 总投入”和“最新有效 attempt 结果”，默认成本使用所有 attempt。

### 3.2.1 测试级完整打点

SWE-bench 官方 `get_eval_tests_report` 已能生成 F2P/P2P 的 success/failure 测试清单，`include_tests_status` 可把 `tests_status` 写入每实例报告；`test_output.txt` 保存完整测试输出。平台必须保留这些信息，而不是只存最终布尔值 resolved。

```text
eval_case_attempt
  ├─ test_case_result：suite/test_name/native_status/outcome/duration
  │                   group=F2P/P2P/...，expected，failure_message
  ├─ score_artifact：report.json、test_output.txt、run_instance.log
  └─ log_range：失败堆栈/标准输出在 OSS 原始日志中的字节或行范围
```

- F2P 全部通过且 P2P 未发生回归，才映射为 resolved；官方 PARTIAL/NO 也保留为原生状态。
- 未出现在解析状态表中的测试不能一律伪造为通过；沿用当前官方评分器语义时，记录 `inferred_by_scorer=true` 和评分器版本。
- 测试未运行、解析为空、测试命令退出非零、F2P skip、P2P skip 等按官方版本分别处理，不能仅根据日志中出现 PASSED 就判 resolved。
- 每个失败测试可打开失败摘要和完整日志；原始 patch、test patch、eval.sh 与测试输出按权限分级。
- 新数据集适配器复用同一 `test_case_result` 契约；没有测试概念的数据集可映射为检查项/评分项，但不得丢弃原生明细。
### 3.3 结果快照

运行中每次有效状态变更可节流重算，写 `is_final=false`；终态事务写 `is_final=true`。最终结果一旦生成不可随 dataset 当前指标配置变化。需要修正算法时创建新 metric version 并显式“重算”，保留原结果与重算审计。

## 4. Runtime 适配器

```python
class RuntimeAdapter(Protocol):
    runtime_key: str
    runtime_version: str
    adapter_version: str

    def validate_assets(self, assets: AssetSnapshot) -> list[Issue]: ...
    def materialize_assets(self, assets: AssetSnapshot, workspace: Path) -> None: ...
    def command(self, task: RuntimeTask) -> CommandSpec: ...
    def consume_stream(self, stream: ByteStream) -> Iterator[LiveEvent]: ...
    def discover_trace_files(self, runtime_home: Path) -> list[TraceFile]: ...
    def trace_parser(self) -> TraceParser: ...
```

适配器版本必须和 Runtime 版本一起冻结。启动命令模板是平台审核代码，不接受用户在 UI 注入任意 shell；模型名、路径等参数通过结构化变量绑定。

### 4.1 资产映射

| 资产 | Claude Code | Codex/OpenCode/pi | 约束 |
|---|---|---|---|
| Skill | `.claude/skills/` | 按各 Runtime 当前约定映射，架构目标为 `.agents/` | 以 SKILL.md 目录为单位，固定版本 fixture 验证 |
| Command | `.claude/commands/` 或原生插件机制 | 按 Runtime 原生格式生成/安装 | OpenSpec 生成型命令需声明前置 |
| Subagent | `.claude/agents/` | 按 Runtime 能力映射 | 不支持时必须提示能力降级，不静默忽略 |

Claude Code 官方 headless 文档指出 `--bare` 会跳过 Skills、Commands、Subagents 等自动发现。因此评测需要这些资产时不能简单加 `--bare`；应使用隔离的 Runtime HOME、明确的工作目录与允许清单来控制加载面，并用测试确认只加载快照资产。

## 5. 启动前一致性检查

```text
Dataset version ready
+ Runtime supports dataset environment
+ Asset snapshot resolved and compatible with Runtime
+ Runtime prerequisites declared and allowed
+ Model endpoint/credential usable and model name allowed
+ Resource pool connection/provider compatible
+ Runner labels match environment and sandbox
+ Scoring image digest available
+ Metric definitions registered
= preparation ready
```

容量不足只产生排队警告，不一定阻塞创建；凭据失效、Ref 未解析、资产不兼容、评分镜像缺失和连接能力不匹配必须阻塞。

## 6. 扩展测试包

每个新适配器至少提交：

- 最小合法来源、非法字段、部分失败和大批量 fixture；
- 一个任务包 golden manifest 与可见性测试；
- 一份评分报告正例/负例和指标 golden；
- Runtime 资产映射的临时目录快照测试；
- 一份主 Agent + 子 Agent 原生轨迹 fixture 和归一化 golden；
- 同输入重复导入、解析器重放和旧版本兼容测试。
