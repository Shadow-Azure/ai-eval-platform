# Cache token 原型（issue #339）

原型改动：`prototype/index.html`。

## 口径

- 总 Token（含缓存）= 非缓存输入 + cache 输入（read + creation）+ 输出。
- 非缓存输入 = LLM span 的 input（不含 cache）；cache 输入来自 LLM span 的
  `gen_ai.usage.cache_read_input_tokens` / `cache_creation_input_tokens`。
- 汇总条、用例行、结果分析、数据集详情/列表、轨迹页统一此口径；数据集累计标注
  “有解析轨迹的评测 N/M 次”，不把缺失轨迹的评测算成 0。
- 原型 mock 数据：cache 比率 12–24× 非缓存输入，仅用于展示；生产值以 trace 为准。

## 截图

`before/` 为改动前（只显示非缓存 input+output），`after/` 为改动后：

| 区域 | Before | After |
|---|---|---|
| 评测用例明细 | 01-eval-cases-before.png | 01-eval-cases-after.png |
| 结果分析 | 02-results-before.png | 02-results-after.png |
| 数据集详情 | 03-dataset-detail-before.png | 03-dataset-detail-after.png |
| 数据集列表 | 04-datasets-list-before.png | 04-datasets-list-after.png |
| 会话轨迹 | 05-trace-before.png | 05-trace-after.png |

生产实现（后端 cache 聚合 + Web 接入）待原型确认后另开 PR；本目录只做原型证据。
