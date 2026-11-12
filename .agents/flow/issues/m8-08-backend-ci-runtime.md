# [m8-08] 优化 Backend & Web CI 的 backend（pytest + migration）执行时长

[English](m8-08-backend-ci-runtime.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m8-08-backend-ci-runtime.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - .github/workflows/backend-ci.yml
  - tests/backend/**
  - backend/README.md
  - pyproject.toml
  - plans/**
adr: []
github:
  number: 196
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/196
```

## 背景

2026-11-12，PR #191（仅改 web 文件）的 Backend & Web CI（[run 43183921424](https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/43183921424)，attempt 2，job 131073760686）中，backend job 耗时 15m08s，pytest 步骤 14m36s，pytest 自报 722 passed in 865.48s（14m25s）；同一次运行的 web、deployment contract、runner 作业分别为 23s、5s、17s。backend job 是 PR 反馈时间的唯一长尾，而且改动与 backend 无关时也要等它跑完。

| 作业 | 耗时 | 备注 |
|---|---|---|
| backend (pytest + migration) | 15m08s | pytest 约 14m25s，容器初始化约 15s，安装约 12s，migration dry-run 约 2s |
| web (typecheck + build) | 23s | |
| deployment contract | 5s | |
| runner (node contract tests) | 17s | |

最近 30 次运行中，14 次成功运行的 backend job 在 660–944s（11–15.7 分钟），pytest 步骤在 631–901s，耗时与改动类型无关；失败或取消的运行只会更短。这说明不是偶发慢，而是每个 PR 的固定成本。

根因在测试隔离方式，而不是测试逻辑本身：该提交收集的 722 个测试中有 550 个使用 `tests/backend/conftest.py` 的 `database_url` fixture；该 fixture 是 function 级，每个测试都会执行 `DROP SCHEMA public CASCADE` → `CREATE SCHEMA public` → 以子进程运行 `alembic upgrade head`（当前 21 个迁移版本）。在本地对 69 个典型 CRUD 测试抽样，setup 合计 28.4s（约 0.41s/个，SQLite），call 合计只有 0.4s，数据库重建占约 98% 的时间；CI 使用 PostgreSQL 且每次迁移要跨连接提交，单项成本更高。按 pytest 总时长和数据库用例数估算，约 9–12 分钟消耗在重复建库与迁移上。

## 目标

- 在讨论中确定目标时长（建议 backend job P95 ≤ 5 分钟；若希望 ≤ 2 分钟则需要并发或分片）。
- 优先消除每个用例的重复数据库迁移，再评估是否需要并发，避免用并发掩盖固定成本。
- 保持 550 个数据库用例仍从未迁移的空库开始，迁移、约束与 downgrade 覆盖不减少。
- 不通过删除测试、放宽断言或跳过数据库用例来换时间。

## 候选方案

### A. 每个会话只迁移一次，用例从模板库克隆（首选）

- 做法：session 启动时创建一次性 template DB 并执行一次 `alembic upgrade head`（本地 SQLite 用复制预迁移文件）；每个用例先 `DROP DATABASE` 再从模板 `CREATE DATABASE ... TEMPLATE ...`，迁移类测试保留独立空库。
- 预期：把每个用例约 1s 的建库 + 迁移压缩到约 0.05–0.2s 的模板克隆，节省约 8–9 分钟，pytest 降到约 3–4 分钟，backend job 约 4 分钟。
- 代价：改 `tests/backend/conftest.py` 的数据库生命周期；需要为 `test_migrations.py` 等 downgrade/upgrade 用例保留真实空库；PostgreSQL 模板库要求没有活动连接。

### B. pytest-xdist 并行

- 做法：引入 pytest-xdist，每个 worker 使用独立数据库或独立模板，按 runner 的 vCPU 数设置 `-n`。
- 预期：在 A 之后可把约 3–4 分钟压到 1–2 分钟；如果不做 A，4 个 worker 约 3–4 分钟，但每个 worker 仍重复迁移，并会争抢同一个 PostgreSQL 与 CPU。
- 代价：新增开发依赖；共享 5432 端口、临时对象存储、固定资源名和模块级状态需要逐个排查；失败输出、重跑策略和 `-p no:randomly` 类顺序假设会变复杂。

### C. CI matrix 分片

- 做法：2–4 个 shard job，各自启一个 PostgreSQL service，用 pytest-split（维护 `.test_durations`）或按测试文件分组。
- 预期：wall time 约为原来的 1/N，但 runner 分钟数乘以 N，且每个 shard 多 20–30s 启动；分片不均时收益下降。
- 代价：分支保护的 required check 名称会从单个 backend job 变成多个，需要仓库设置变更；时长文件需要持续维护。

### D. 按变更路径跳过无关作业（正交）

- 做法：增加 changes 过滤 job，backend job 只在 `backend/**`、`tests/backend/**`、`pyproject.toml`、`docker-compose.yml`、`deploy/**` 或 workflow 自身变更时运行；`web/**` 单独变更时跳过。
- 预期：web-only PR（如 #191）直接省掉整个 13 分钟，对 backend 改动的覆盖不变。
- 代价：需要确认共享文件清单；required check 被 skip 时的合并语义要验证；workflow 的 `on.pull_request.paths` 与 job 级过滤要避免相互矛盾。

### E. 低成本补充

- PostgreSQL service 增加 `fsync=off`、`synchronous_commit=off` 与合理的 `max_wal_size`（一次性测试库可接受），可缩短 DDL 提交时间。
- `actions/setup-python` 打开 pip cache（约 8–10s）；migration dry-run 目前仅 2s，不是瓶颈，可保留为独立检查。

## 建议顺序

1. 先在 CI 加 `pytest --durations=30`（或临时日志时间戳）确认每个用例的 setup/call 拆分，与本文估算对齐。
2. 实施 A（模板库），同一次 PR 内对比改动前后的 job 与 pytest 时长；这是收益最大且风险最低的一步。
3. 与 A 独立地实施 D，让 web-only PR 不再触发 backend job。
4. 如果 A + D 仍达不到讨论确定的目标，再选 B（共享 service 并行）或 C（matrix 分片）；两者都要先确认 runner 规格与 required check 配置。

## 决定

- 2026-11-12：按先 A 后 D 实施；A 为会话级模板库 + 每用例克隆，D 为 web-only PR 跳过 backend job；B/C 暂不启用。
- 实现与实测对比见 PR #197。

## 实测

| 指标 | 改动前 | 改动后（A） |
|---|---|---|
| backend job | 15m08s | 5m13s |
| pytest 步骤 | 14m36s | 4m36s |
| pytest 自报 | 722 passed in 865.48s | 728 passed in 263.32s |

- 改动前：[run 43183921424](https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/43183921424) / job 131073760686；改动后：[run 43193007271](https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/43193007271) / job 131096856802。
- 本地 SQLite 完整套件 2m15s；PostgreSQL 的 `CREATE DATABASE ... TEMPLATE` 克隆成本已不在最慢 20 项内。
- D 按 PR #191 的文件列表验证：全部为 `web/**` 时 changes job 输出 `backend=false`，backend job 被跳过；backend 相关改动仍输出 `true`。

## 待讨论

- 目标时长最终定为多少：5 分钟、3 分钟还是 2 分钟？
- 若 A 实测仍不达标，是否启用 B（pytest-xdist）或 C（matrix 分片）？
- 是否需要把慢测试单独标记并分作业运行？

## 参考

- pytest-xdist 并行执行：https://pytest-xdist.readthedocs.io/en/stable/
- pytest-split 按时长分片：https://github.com/jerry-git/pytest-split
- PostgreSQL `CREATE DATABASE ... TEMPLATE`：https://www.postgresql.org/docs/current/sql-createdatabase.html
- GitHub Actions matrix：https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs
- 同类模板库实践（evoila/meho）：https://github.com/evoila/meho/pull/898

## 验收

- 记录同一 PR 或同一提交在改动前后的 backend job 与 pytest 步骤耗时，附 CI 运行链接。
- 550 个数据库用例仍各自从已迁移的空库开始，迁移、约束与 downgrade 测试保持覆盖且通过。
- 记录实测耗时；若达到建议的 ≤5 分钟则关闭，否则按实测决定是否启用 B/C；web-only PR 不再触发 backend job。
- 测试数量不减少，数据库用例不被跳过；失败输出仍能定位到具体用例。
