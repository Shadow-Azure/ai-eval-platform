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
