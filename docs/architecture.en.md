# Architecture Map

English | [中文](architecture.md)

The repository is entering M4: the execution foundation and the real evaluation loop are in place, and concurrency scheduling, timeouts and cancellation, budget pausing, and the elastic resource pool are being completed.

## Components

| Area | Responsibility | Boundary |
|---|---|---|
| `docs/` | Current facts, development method, and bilingual contract | No roadmaps or decision debates |
| `.agents/flow/` | roadmap, milestone, issue state machine | Flow state only |
| `scripts/` | Mechanical documentation and workflow gates | Not product runtime |
| `.github/workflows/` | CI decisions for docs, flow, tests, and deployment | Not a local debugging substitute |
| `backend/` | FastAPI control plane, worker, migrations, and identity/dataset/evaluation services | No agent execution and no plaintext secrets |
| `web/` | React console: login, app shell, evaluation, and operations status pages | Reaches the control plane only through same-origin `/api` |
| `runner-contract/` | Runner dispatch, manifest, heartbeat, and callback schemas | Contract only |
| `runner/` | Reusable workflow, synthetic task, and contract tests | No dataset- or agent-specific branches |

## Documentation pipeline

```text
Markdown base + English pair + pairing record
                 |
                 v
index -> budgets -> wrap -> pairing -> terminology
                 |
                 v
             warnings on local
                 |
                 v
      links + self-tests in CI
```

The Chinese file is `foo.md`, the English side is `foo.en.md`, and the record is `foo.i18n.yaml`. The record stores both blob hashes from the last confirmed state, so drift on either side fails the pairing gate.

## Identity and sessions

Login uses an account and password; passwords are stored as salted scrypt hashes, the session cookie carries an HMAC-signed token with HttpOnly and SameSite=Lax, and the database stores only the token hash and revocation state. `/api/v1/auth/*` manages sessions, `/api/v1/*` business routes require a session, and the Runner plane keeps its own short-lived tokens. Seed creates the default administrator idempotently from `AUTH_BOOTSTRAP_ADMIN_*`, and writes are administrator-only.

## Operations status

An administrator can open `/operations` to see liveness results for the control-plane API, PostgreSQL, Redis, object storage, the Runner plane, the dispatch worker, and the model gateway, plus resource pools, Runners, datasets, model configurations, the dispatch queue, and database status. A read-only member does not see it in navigation; the page refreshes every 30 seconds.

## Runner contract

M2 decouples execution from datasets and agents: the platform sends eval_run, shard, attempt, manifest URL, and digest through repository_dispatch; the runner verifies them before execution, then reports heartbeats, upload manifests, and completion or failure callbacks. `runner-contract/` holds the versioned schemas, while `runner/` holds the workflow template, synthetic task, and contract tests.

The platform keeps five timeouts (queueing, execution, scoring, upload, heartbeat); cancellation stops new dispatch, and the worker's reconciler backfills lost completions. Resource connections and spot pools scale from the queue gap; a price above the threshold only queues work.

## Extension points

- After the product runtime lands, add its components, data flows, and external dependencies here.
- Development tasks proceed through `.agents/flow/`; that directory is authoritative for flow state and change scopes.
- Add platform-specific checks as leaves under `scripts/`, combine them in `run-gates`, and wire them into CI.
- If documentation volume grows, reorganize layers and budgets before considering a site projection.
