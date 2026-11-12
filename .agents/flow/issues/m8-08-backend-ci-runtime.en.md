# [m8-08] Shorten the backend (pytest + migration) job in Backend & Web CI

English | [中文](m8-08-backend-ci-runtime.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
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

## Background

On 2026-11-12, in the Backend & Web CI run for PR #191 (a web-only change) ([run 43183921424](https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/43183921424), attempt 2, job 131073760686), the backend job took 15m08s and the pytest step 14m36s; pytest itself reported 722 passed in 865.48s (14m25s), while the web, deployment contract and runner jobs in the same run took 23s, 5s and 17s. The backend job is the only long tail in PR feedback, and it still runs when the change has nothing to do with the backend.

| Job | Duration | Notes |
|---|---|---|
| backend (pytest + migration) | 15m08s | pytest about 14m25s, container init about 15s, install about 12s, migration dry-run about 2s |
| web (typecheck + build) | 23s | |
| deployment contract | 5s | |
| runner (node contract tests) | 17s | |

Among the last 30 runs, the 14 successful backend jobs took 660–944s (11–15.7 minutes) and the pytest step took 631–901s, regardless of what changed; failed or cancelled runs are only shorter. This is a fixed cost per PR, not an occasional slowdown.

The root cause is the test isolation strategy, not the test logic itself: 550 of the 722 tests collected at that commit use the `database_url` fixture from `tests/backend/conftest.py`; it is function-scoped, so every test runs `DROP SCHEMA public CASCADE` → `CREATE SCHEMA public` → `alembic upgrade head` in a subprocess (21 migration revisions today). A local sample of 69 typical CRUD tests shows 28.4s of setup (about 0.41s each on SQLite) against only 0.4s of call time, so database rebuilds account for about 98% of the time; CI uses PostgreSQL and each migration commits over a connection, so the per-test cost is higher. Based on the total pytest time and the number of database tests, roughly 9–12 minutes go to repeated database creation and migration.

## Goals

- Agree on a target duration in the discussion (suggested: backend job P95 ≤ 5 minutes; ≤ 2 minutes requires parallelism or sharding).
- Remove the per-test database rebuild first, then decide whether parallelism is needed, so that parallelism does not hide a fixed cost.
- Keep all 550 database tests starting from a freshly migrated empty database and keep migration, constraint and downgrade coverage intact.
- Do not buy time by deleting tests, weakening assertions or skipping database tests.

## Candidate options

### A. Migrate once per session and clone a template database per test (preferred)

- Approach: create a disposable template DB at session start and run `alembic upgrade head` once (copy a pre-migrated file for SQLite locally); each test runs `DROP DATABASE` then `CREATE DATABASE ... TEMPLATE ...`, while migration tests keep a separate empty database.
- Expected: reduce the roughly 1s database creation and migration per test to roughly 0.05–0.2s of template cloning, saving about 8–9 minutes and bringing pytest to about 3–4 minutes and the backend job to about 4 minutes.
- Cost: change the database lifecycle in `tests/backend/conftest.py`; keep a real empty database for downgrade/upgrade tests such as `test_migrations.py`; a PostgreSQL template database must have no active connections.

### B. pytest-xdist parallelism

- Approach: add pytest-xdist, give each worker its own database or template, and set `-n` from the runner vCPU count.
- Expected: after A, cut about 3–4 minutes to 1–2 minutes; without A, four workers take about 3–4 minutes but every worker still repeats migrations and competes for the same PostgreSQL and CPU.
- Cost: a new development dependency; shared port 5432, temporary object storage, fixed resource names and module-level state must be audited one by one; failure output, rerun strategy and ordering assumptions become more complex.

### C. CI matrix sharding

- Approach: 2–4 shard jobs, each with its own PostgreSQL service, using pytest-split (maintaining `.test_durations`) or file-based groups.
- Expected: wall time drops to about 1/N, but runner minutes multiply by N and each shard pays 20–30s of startup; uneven splits reduce the gain.
- Cost: the required check name changes from one backend job to several, which needs a repository settings change; the durations file needs ongoing maintenance.

### D. Skip unrelated jobs by changed path (orthogonal)

- Approach: add a changes-filter job so the backend job runs only when `backend/**`, `tests/backend/**`, `pyproject.toml`, `docker-compose.yml`, `deploy/**` or the workflow itself changes; skip it for changes under `web/**` only.
- Expected: web-only PRs such as #191 save the whole 13 minutes with no loss of coverage for backend changes.
- Cost: agree on the shared-file list; verify how a skipped required check affects merging; keep `on.pull_request.paths` and job-level filtering from contradicting each other.

### E. Low-cost additions

- Add `fsync=off`, `synchronous_commit=off` and a reasonable `max_wal_size` to the PostgreSQL service (acceptable for a disposable test database) to shorten DDL commits.
- Enable the pip cache in `actions/setup-python` (about 8–10s); the migration dry-run currently takes only 2s and is not a bottleneck, so keep it as an independent check.

## Suggested order

1. First add `pytest --durations=30` in CI (or temporary log timestamps) to confirm the setup/call split per test and align it with this estimate.
2. Implement A (template database) and compare the job and pytest durations before and after in the same PR; this is the highest-yield, lowest-risk step.
3. Implement D independently of A so that web-only PRs no longer trigger the backend job.
4. If A + D still miss the agreed target, choose B (shared-service parallelism) or C (matrix sharding) after confirming the runner size and required-check configuration.

## Decision

- 2026-11-12: implement A first, then D; A is a session-level template database with a per-test clone, D skips the backend job for web-only PRs, and B/C stay disabled for now.
- Implementation and measured comparison are in PR #197.

## Measurements

| Metric | Before | After (A) |
|---|---|---|
| backend job | 15m08s | 4m38s |
| pytest step | 14m36s | 4m07s |
| pytest self-report | 722 passed in 865.48s | 728 passed in 238.10s |

- Before: [run 43183921424](https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/43183921424) / job 131073760686; after: [run 43194162421](https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/43194162421) / job 131100771689.
- The local SQLite suite takes 2m15s; the PostgreSQL `CREATE DATABASE ... TEMPLATE` clone cost no longer appears in the slowest 20 durations.
- D was verified against the file list of PR #191: when every file is under `web/**`, the changes job outputs `backend=false` and the backend job is skipped; backend-related changes still output `true`.

## Open questions

- What is the final target duration: 5 minutes, 3 minutes or 2 minutes?
- If A alone still misses the target, do we enable B (pytest-xdist) or C (matrix sharding)?
- Do we need to mark slow tests and run them in a separate job?

## References

- pytest-xdist parallel execution: https://pytest-xdist.readthedocs.io/en/stable/
- pytest-split duration-based sharding: https://github.com/jerry-git/pytest-split
- PostgreSQL `CREATE DATABASE ... TEMPLATE`: https://www.postgresql.org/docs/current/sql-createdatabase.html
- GitHub Actions matrix: https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs
- A comparable template-database practice (evoila/meho): https://github.com/evoila/meho/pull/898

## Acceptance

- Record the backend job and pytest step durations before and after for the same PR or commit, with CI run links.
- All 550 database tests still start from a migrated empty database, and migration, constraint and downgrade coverage stays intact and passing.
- Record the measured duration; close if it reaches the suggested ≤ 5 minutes, otherwise use the result to decide whether B/C is needed; web-only PRs no longer trigger the backend job.
- The test count does not shrink and database tests are not skipped; failures still identify the failing test.
