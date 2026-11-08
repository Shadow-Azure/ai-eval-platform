# 业界「AI Agent（编程类）评测平台/框架」开源方案与架构模式调研

> 标注：【验证】= 已亲自 fetch 页面确认；【摘要】= 仅搜索摘要，未逐页验证。时间：2026-11-08。

## 结论先行

1. **最值得借鉴的现成组合是 OpenHands 三仓架构 + Harbor**：前者用「GitHub Actions 工作流当触发器/执行编排 + GCS 存结果 + 静态 Dashboard」证明了"评测平台 + GitHub Actions Runner 资源池"路线可行且已有生产级实践；后者（Terminal-Bench 团队新作）已把「数据集版本化 + 多 Agent CLI 适配（Claude Code/Codex/OpenCode）+ 沙箱提供商抽象（Docker/Daytona/Modal）」做成通用框架，几乎正中你的需求。
2. **评测数据集 schema 事实标准就是 SWE-bench instance 格式**（`instance_id/repo/base_commit/patch/test_patch/FAIL_TO_PASS/PASS_TO_PASS`），自建平台应直接兼容，再向上泛化出"结果指标泛化层"。
3. **商用平台（Braintrust/LangSmith/Langfuse/Weave/Galileo/OpenRouter）都不适合做编程 Agent 任务评测的主体**，但它们的 trace 存储、evaluator、多租户数据模型值得抄。
4. **没有现成开源的"评测平台 + GH Actions Runner 池"一站式产品**；OpenHands/evaluation、google-gemini/gemini-cli 的 PR #8028（把 SWE-bench harness 塞进 GitHub Actions）是最近的先例。平台层（数据集管理、多模型/API Key 管理、实时进度、多形态指标展示）需自建。

## 1. SWE-bench 官方 harness【验证】

- Repo：[swe-bench/swe-bench](https://github.com/SWE-bench/SWE-bench)；数据集在 HuggingFace（`SWE-bench/SWE-bench[_Lite|_Verified|_Multimodal|_Multilingual]`），schema 见[官方 datasets 文档](https://www.swebench.com/SWE-bench/guides/datasets/)：每实例含 `instance_id`(owner__repo-pr号)、`repo`、`base_commit`、`problem_statement`、`patch`（金标解）、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS`，Multimodal 变体加 `image_assets`。
- 执行：入口 `swebench.harness.run_evaluation`，输入为 JSONL 预测（`instance_id` + `model_patch`）；每实例一个 Docker 容器（按 repo@commit 预构建镜像，`--cache_level` 控制 base/env/instance 三级缓存），也可跑在 Modal 云上或用 [sb-cli](https://github.com/swe-bench/sb-cli) 提交。
- 评分：apply patch → 跑测试 → `FAIL_TO_PASS` 全过且 `PASS_TO_PASS` 不回退即为 resolved，指标为 **resolve rate**（resolved/submitted）；每实例产出 `report.json/test_output.txt/patch.diff`（见 [evaluation 文档](https://www.swebench.com/SWE-bench/guides/evaluation/)）。
- 衍生：multimodal 在同 repo 的 datasets；Java/多语言衍生是 [multi-swe-bench/multi-swe-bench](https://github.com/multi-swe-bench/multi-swe-bench)【摘要】，schema 照搬。

## 2. OpenHands 评测框架【验证】

- 现为三仓结构：[software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)（SDK + `run-eval.yml` 触发 workflow）、[evaluation](https://github.com/OpenHands/evaluation)（`eval-job.yml` 编排）、[benchmarks](https://github.com/OpenHands/benchmarks)（**benchmark runner 即每个 `benchmarks/<name>/` 子目录**：swebench/swebenchpro/gaia/commit0/openagentsafety/programbench，各自含数据集加载、Docker 镜像构建 `build_images.py`、runner、评分与 `output.report.json`）。
- 触发：PR label（`run-eval-50/200/500`）/ release / 手动 dispatch；结果存 GCS（`{benchmark}/{model_slug}/{run_id}/`：`params.json` + `output.report.json` + `cost_report.jsonl` + `results.tar.gz`），CDN + 每日 metadata 清单 + Vercel 静态 Dashboard（[eval-infrastructure 文档](https://github.com/OpenHands/software-agent-sdk/blob/v1.24.0/.agents/skills/manage-evals/references/eval-infrastructure.md)）。
- 执行隔离：本地 Docker workspace，或 remote runtime API 云端容器（镜像按 SDK commit SHA 预构建推 ghcr，32+ 并发）。结果 schema 泛化为 `{total,submitted,completed,resolved,empty_patch,error}_instances/ids`。

## 3. 其他同类

- **SWE-agent**【验证】：Princeton/Stanford，MIT，单 yaml 配置 Agent-Computer Interface；官方已推荐更极简的 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)；指标同为 resolve rate。
- **Terminal-Bench / Harbor**【验证】：[terminal-bench](https://github.com/laude-institute/terminal-bench)（任务=instruction+测试脚本+oracle 解，pip 包 `tb`，Docker 沙箱）；演进为 [harbor](https://github.com/laude-institute/harbor)：`harbor run -d <dataset@version> -a <claude-code|codex|openhands> -m <model> --env <docker|daytona|modal…>`，benchmark 以 adapter 插件化，沙箱以 provider 插件化——**与你的"Agent 运行时 × 数据集 × skill"矩阵模型完全同构，强烈建议作为执行器层底座**。
- **SWE-rebench**【摘要】：SWE-bench 团队的持续更新+去污染流水线（arXiv 2505.20411），按月生成新实例并做 n-gram 去污染。
- **LiveCodeBench**【摘要】：[livecodebench/livecodebench](https://github.com/livecodebench/livecodebench)，按发布日期滚动出新题防污染，指标为 pass@1 类。
- **AgentBench**【摘要】：THUDM，8 类环境（OS/DB/卡牌等）统一 agent loop，按环境成功率加权总分。
- **OSWorld**【摘要】：[xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld)，真实 Ubuntu VM（QEMU/KVM），指标为 step-level 任务完成率。

## 4. 商用/平台型方案【多为摘要】

- **Langfuse**（open-core，可自托管）：evals 支持 LLM-as-a-judge / user feedback / 自定义，强项是 trace+成本观测，不是编程任务评测（[evals 文档](https://langfuse.com/docs/evaluation)）。
- **Braintrust / LangSmith / Galileo / W&B Weave**：托管 eval 平台，模型幻觉类/对话类 eval 强，均无"Docker 沙箱里跑 agent 修 bug"的任务执行能力；可借鉴其 dataset 版本化、evaluator 管线、多租户组织-项目数据模型。
- **OpenRouter Ori Eval**【摘要】：基于你自选提示词集的偏好对战评测，非编程任务。
- **OpenCompass / EvalScope / ModelScope**【EvalScope 已验证】：[open-compass/opencompass](https://github.com/open-compass/opencompass)【摘要】面向学科/理解类基准；[modelscope/evalscope](https://github.com/modelscope/evalscope) 已内置 SWE-bench 系列 + **External Agent Bridge（直接驱动 Claude Code、Codex CLI，透明转发 LLM 流量并录制 agent_trace）+ OpenCode/OpenHands runner + Docker 沙箱 + React Dashboard**，是中文生态里最接近你需求的框架，可作 CLI/执行层或对照架构。

## 5. 「评测平台 + GitHub Actions Runner 池」现成项目

无开箱即用的开源平台。最接近的先例：**OpenHands 三仓**（见 §2，Actions 负责触发、镜像构建、结果上传，self-hosted runner 即算力池）；其次 [gemini-cli PR #8028](https://github.com/google-gemini/gemini-cli/pull/8028)【摘要】把 SWE-bench harness 接入 GitHub Actions。结论：平台层必须自建，Actions 层可直接复用该模式。

## 6. 可复用架构模式 & 需自建清单

**可借鉴**：①数据集插件化+版本化（SWE-bench HF datasets / harbor `dataset@version`）；②结果 schema 泛化（OpenHands `output.report.json`：completed/resolved/error/empty_patch）；③执行器三段抽象——Agent CLI 适配层（harbor agents / EvalScope bridge）、沙箱抽象层（harbor env providers / OpenHands local-vs-remote workspace）、镜像预构建与缓存（SWE-bench cache_level、ghcr 按 commit 打 tag）；④用 GitHub Actions 工作流当"触发+编排+资源池"；⑤对象存储 + 每日索引清单 + 静态 Dashboard 做结果展示；⑥按 run_id 缓存实例级评分。

**需自建**：多模型/API Key 的凭据管理（MiniMax/DeepSeek/GLM/Kimi/MiMo 的 base_url+key 配置中心，EvalScope 的 TaskConfig 形态可参照）；「Agent 运行时 × 开发 skill」组合矩阵的实验管理；实时进度（实例级 heartbeat，现有框架多为跑完上传）；按数据集自定义指标形态的前端；单租户起步、组织-工作区-项目三层预留多租户；执行配额与失败重试策略。

## 项目对比表

| 项目 | 维护方 | 活跃度 | License | 自定义数据集 | 多 Agent 支持 | 执行隔离 | 可借鉴点 |
|---|---|---|---|---|---|---|---|
| swe-bench harness | Princeton/SWE-bench | 高 | MIT | HF datasets 即插 | 不绑定 agent（只要产 patch） | Docker/Modal 沙箱+镜像缓存 | 数据集 schema、评分逻辑 |
| OpenHands benchmarks | All Hands AI | 高 | MIT | benchmarks/ 每目录一个 runner | SDK agent | Docker / remote runtime | runner 目录约定、report schema、Actions 编排 |
| Harbor | Laude Institute | 高(新) | 待查 | adapter 插件化 | Claude Code/Codex/OpenHands 等 CLI | Docker/Daytona/Modal 等 provider | Agent×数据集矩阵抽象（核心参考） |
| SWE-agent / mini | Princeton 等 | 中 | MIT | 配置 yaml | 自带 ACI | Docker（SWE-ReX） | agent 接口最小化 |
| Terminal-Bench | Laude Institute | 中 | MIT | tasks/ 目录 | 多 agent adapter | Docker | 任务定义(instruction+测试) |
| SWE-rebench | SWE-bench 团队 | 中 | MIT | 流水线自动产出 | 同 harness | Docker | 去污染+持续更新机制 |
| LiveCodeBench | 社区 | 中 | MIT | 官方滚动发布 | 任意 | 本地执行 | 时间切分防污染 |
| AgentBench / OSWorld | THUDM / XLANG | 低-中 | Apache/MIT | 各自环境定义 | 各自 agent loop | Docker / VM | 多环境统一指标加权 |
| EvalScope | 阿里 ModelScope | 高 | Apache-2.0 | 支持 | Claude Code/Codex/OpenCode bridge | Docker 沙箱 | Agent Bridge、Dashboard、中文生态 |
| OpenCompass | 上海AI Lab | 高 | Apache-2.0 | 支持 | 弱 | 本地 | 基准注册机制 |
| Langfuse | Langfuse | 高 | MIT/open-core | 支持 | 任意（trace 层） | 无沙箱 | 自托管、evaluator、多租户模型 |
| Braintrust/LangSmith/Galileo/Weave | 各厂商 | 高 | 商业/部分开源 | 支持 | trace 层 | 无沙箱 | 数据模型与 eval 管线 UX |
