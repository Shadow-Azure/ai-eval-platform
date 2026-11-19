# m8-53 原型 Token 统计修改前后截图

本目录保存 issue #332 的原型修改证据，基线为 `main@f651543`。

## 场景

- 打开 `prototype/index.html`，使用原型账号 `admin@acme.local` / `eval-demo` 登录。
- 用例集：`#/evals/eval-001?tab=cases`
- 数据集详情：`#/datasets/swebench-verified`
- 数据集目录：`#/datasets`
- 视口 1440×1000，Chrome，2× DPR；截图只保留相关卡片，去除登录提示。

## 文件

| 文件 | 修改前 | 修改后 |
|---|---|---|
| `01/02-eval-cases` | 用例明细只有每用例 Token 列 | 新增用例集总 Token、输入/输出、平均、成本汇总条 |
| `03/04-dataset-detail` | 数据集详情没有 Token 汇总 | 新增累计 Token、平均/用例、平均/评测、累计成本四张卡，用例预览新增平均 Token 列 |
| `05/06-datasets-list` | 数据集卡片只有版本/用例数/核心指标 | 卡片新增累计 Token 与平均每次评测 Token |

> 原型数据为本地 mock，用于验证信息架构与统计口径；真实数值来自生产 trace 的 `input_tokens + output_tokens` 聚合。

## 生产前端缺失对照（2026-11-19 复核）

生产环境 `http://47.98.144.243:8080`，版本 `0075120`，数据集 `SWE-bench Verified 复现`（6 用例）。

| 文件 | 位置 | 生产现状 |
|---|---|---|
| `production/01-prod-results-cards.jpg` | 结果分析 | 只有解决率 / Resolved 数 / 执行失败率 4 张卡，无 Token、无成本 |
| `production/02-prod-cases-table.jpg` | 用例明细 | 列为用例 / session / 执行 / 结果 / 轨迹 / 测试打点 / 耗时，无 Token/成本列 |
| `production/03-prod-config-metrics.jpg` | 指标口径 | 只有 4 个解决率类指标，无 `tokens_per_case` / `cost_per_case` |
| `production/04-prod-dataset-detail.jpg` | 数据集详情 | 卡片只有版本/用例数/指标/适配器；用例预览无 Token |
| `production/05-prod-dataset-list.jpg` | 数据集列表 | 卡片只有版本/用例数/核心指标，无累计 Token |
| `production/06-prod-trace.jpg` | 会话轨迹 | 唯一有 Token 的位置：单次 attempt 60.9 万 tokens / ¥2.56 |

结论：生产只有“单次轨迹级”Token，缺少“用例集级”汇总与“数据集级”聚合；原型修改定义了目标形态。
