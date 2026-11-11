# Architecture Map

English | [中文](architecture.md)

The repository is entering M5: the execution foundation and the real evaluation loop are in place, and model credentials, Git assets, resource pools, and result browsing are being organized into complete Web product workflows.

## Components

| Area | Responsibility | Boundary |
|---|---|---|
| `docs/` | Current facts, development method, and bilingual contract | No roadmaps or decision debates |
| `.agents/flow/` | roadmap, milestone, issue state machine | Flow state only |
| `scripts/` | Mechanical documentation and workflow gates | Not product runtime |
| `.github/workflows/` | CI decisions for docs, flow, tests, and deployment | Not a local debugging substitute |
| `backend/` | FastAPI control plane, worker, migrations, and identity/dataset/evaluation services | No agent execution and no plaintext secrets |
| `web/` | React console: login, app shell, model credentials, evaluation, and operations status pages | Reaches the control plane only through same-origin `/api` |
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

## Models and credentials

An administrator maintains model configurations and encrypted credentials at `/models`: the list shows provider, endpoint, model name, API protocol, pricing, mask, and recent use; create and edit can bind an existing credential or write a new key; key replacement, credential revocation, and connectivity tests happen on the same page. Only administrators perform writes; ordinary members are read-only. Configuration changes appear immediately in the launch wizard's model choices, and an unusable credential becomes a blocking item in preflight.

## Runner contract

M2 decouples execution from datasets and agents: the platform sends eval_run, shard, attempt, manifest URL, and digest through repository_dispatch; the runner verifies them before execution, then reports heartbeats, upload manifests, and completion or failure callbacks. `runner-contract/` holds the versioned schemas, while `runner/` holds the workflow template, synthetic task, and contract tests.

The platform keeps five timeouts (queueing, execution, scoring, upload, heartbeat); cancellation stops new dispatch, and the worker's reconciler backfills lost completions. Resource connections and spot pools scale from the queue gap; a price above the threshold only queues work.

Spot pools are managed by a provisioner: the worker decrypts the tenant connection's Aliyun AccessKey, queries regional spot price and inventory, and creates Spot ECS instances for the queue gap. Each instance registers as an ephemeral runner through a one-time JIT config bound to the pool labels. Price, instance state, spot interruption, heartbeat loss, and creation timeout are recorded as resource events while the queue stays diagnosable; scale-down deregisters the runner before releasing the instance.

Production deployment binds the Runner callback entry to the VPC private address (`API_BIND_ADDRESS`), the security group allows port 8000 only from the VPC CIDR and never from the internet, and the fixed runner keeps its loopback path; `RUNNER_CALLBACK_BASE_URL` defaults to the address derived from `API_BIND_ADDRESS` and is injected into api and worker (an explicit value overrides it) so spot instances can fetch the manifest and post results, and by default download the actions-runner archive from the fixed `/runner/v1/runner-tarball/<version>.tar.gz` endpoint, which the api streams from OSS with platform credentials and gates on the heartbeat token, so the address does not expire with a presign and the bucket stays private; `RUNNER_SPOT_RUNNER_TARBALL_URL` explicitly overrides it with an external mirror. The same release pipeline builds a deterministic md5-named scripts bundle from `runner/` and `runner-contract/schemas/`, caches it in the same private OSS bucket, and serves it to runners through `/runner/v1/scripts/<name>`; prepare/execute prefer the bundle and use `actions/checkout` only as a fallback, so checkout is no longer the primary path. The pool's `labels_json.runs_on` determines both the runner registration labels and the shard dispatch/manifest `runs_on`, so a custom-label pool receives execute jobs; without it the `self-hosted,linux,eval` default applies. The `/resources` provisioning test is a creation check whose instance is released before runner registration; a real evaluation verifies the full chain.

## Agent asset snapshots

`/assets` manages GitHub asset sources: it resolves the Ref, discovers Skills (directories with `SKILL.md`), Commands, and Subagents, and freezes the selected assets into an immutable OSS bundle recording the commit, tree digest, file digests, and manifest. Evaluations reference the frozen snapshot; without an asset source they use only runtime built-ins. The runner maps the snapshot to `.claude/` under an isolated HOME without re-fetching the branch or loading other repository or host assets. Git tokens are encrypted and the console only shows a mask.

## Trace capture

At case completion the runner archives native Claude Code files: root/subagent session JSONL, `subagents/*.meta.json` relation metadata, and stdout/stderr diagnostics enter an attempt-bound multi-file manifest; files produced before a failure are retained. Each entry records a safe logical path, artifact kind, digest, size, content type, runtime and format versions, while object keys stay platform-generated. The backend verifies the callback token's attempt binding, trace-object ownership, safe paths, and digest format, then merges and deduplicates by object key; content sha256 verification is part of later ingestion, and legacy single-file `trace_manifest` payloads stay readable. The runner marks capture incomplete when the root session or subagent metadata is missing, and the backend never lets diagnostics alone claim completeness; `degraded` is written by later parsing, so partial capture is never presented as complete.

## Extension points

- After the product runtime lands, add its components, data flows, and external dependencies here.
- Development tasks proceed through `.agents/flow/`; that directory is authoritative for flow state and change scopes.
- Add platform-specific checks as leaves under `scripts/`, combine them in `run-gates`, and wire them into CI.
- If documentation volume grows, reorganize layers and budgets before considering a site projection.
