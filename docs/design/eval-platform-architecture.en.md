# AI Evaluation Platform Architecture

English | [中文](eval-platform-architecture.md)

This document is the finalized architecture design. It covers functional scope, overall structure, execution flow, data model, concurrency capacity, Aliyun deployment, and CI/CD, addressing all eleven requirements. The research process and candidate comparisons live in the `research/` directory of this repository; the formal document records only the finalized decisions.

## 1. Goals and Scope

The platform standardizes the evaluation of AI coding agents: it manages evaluation datasets and cases, configures evaluated models and API keys, launches evaluations by combining agent runtime × agent asset source × model × resource pool, shows live progress and dataset-specific result metrics, and lets users review the full session traces of the primary and sub agents from any case.

- Username and password login; single tenant at the start, with the data model reserving multi-tenant support (organization, workspace, project).
- Pluggable datasets: SWE-bench is supported first, and adding a new benchmark only requires a dataset adapter plus registered metric definitions.
- Centralized model and credential management, supporting MiniMax, DeepSeek, GLM, Kimi, Xiaomi MiMo, and any OpenAI-compatible endpoint.
- The resource pool reuses GitHub Actions self-hosted runners, mixing a Mac mini and Aliyun Linux machines.
- Evaluations run concurrently; the concurrency equals the number of online runners and scales horizontally.
- Session traces are normalized into the database, raw files are archived in object storage, and the frontend supports case-level drill-down.

## 2. Overall Approach

The platform layer is fully self-built; evaluation scheduling reuses GitHub Actions with self-hosted runners; scoring reuses the official SWE-bench harness Docker images; trace normalization semantics follow OpenInference and the OpenTelemetry GenAI conventions. Table 1 lists the technology choices.

Table 1 Technology choices

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React + Ant Design | Admin UI and session trace viewer |
| Backend | Python + FastAPI | Same ecosystem as the SWE-bench harness, async-friendly |
| Database | Aliyun RDS PostgreSQL | Business data and normalized traces in one database, JSONB for flexible fields |
| Cache and locks | Redis | Progress push buffer, dispatch dedup, task mutual exclusion |
| Object storage | Aliyun OSS | Raw session files, evaluation artifacts, image cache layers |
| Execution scheduling | GitHub Actions self-hosted runners | repository_dispatch with matrix sharding in a private repository |
| Sandbox | Docker (Linux), tart (macOS) | One case per sandbox, the baseline for isolating untrusted code |
| Scoring | swe-bench harness | Official scoring images; the platform only parses reports |
| Deployment | Aliyun ECS + Docker Compose | Pipeline deploys automatically after merging into main |

## 3. Overall Architecture

The platform has five parts: the web frontend, the API service, the dispatcher, data storage, and the execution resource pool. The API service handles all business reads and writes; the dispatcher shards each evaluation into case batches, triggers execution, receives heartbeats, and collects results; the resource pool consists of a generic workflow in a GitHub repository plus self-hosted runners attached to various machines. See Figure 1.

```text
                     +--------------------------------------+
                     |                Aliyun               |
                     |  +--------------------------------+ |
                     |  | ECS: platform (docker compose) | |
                     |  |  - Web   (React + Ant Design)  | |
                     |  |  - API   (FastAPI)             | |
                     |  |  - Dispatcher (dispatch/collect)| |
                     |  |  - SSE progress push           | |
                     |  +--------------------------------+ |
                     |  RDS PostgreSQL  business + traces  |
                     |  Redis           cache / locks      |
                     |  OSS             raw / artifacts    |
                     +-----------------+------------------+
                                       | repository_dispatch + callback API
                     +-----------------+------------------+
                     | GitHub: eval-runner repo (private) |
                     | workflow: matrix (<=256) 1 job/case|
                     +-----------------+------------------+
                                       | runs-on labels
            +--------------------------+---------------------------+
            | Mac mini runners         | Aliyun Linux runners      |
            | (ephemeral + tart VM)    | (ephemeral + Docker)      |
            +--------------------------+---------------------------+
```

Figure 1 Overall architecture

## 4. Evaluation Execution Flow

1. The user selects a dataset version, agent runtime, agent asset source with an editable ref, model credential, and resource pool in the platform; submitting creates an evaluation (eval_run), and cases are written to the case table in batches of at most 256.
2. For each batch the dispatcher calls the GitHub REST API to send a repository_dispatch event whose payload carries the evaluation ID, the case list of the batch, the callback address, and the timeout.
3. Once a runner picks up the task, the in-job runner script starts: it fetches the case and environment definition, prepares the code repository inside the sandbox, drives the agent runtime in headless mode (Claude Code first, using stream-json output), and then collects the patch and session files produced by the model.
4. The official SWE-bench scoring image runs inside the sandbox to decide whether the case is resolved and to produce test output; the in-job script uploads the scoring report, the artifact bundle, and the raw session files to OSS, and calls the platform callback API to report the case status and summary metrics.
5. The platform writes the callback content into the case table, while an importer normalizes the raw session files into session and span records; the raw files stay in OSS.
6. The dispatcher updates live progress based on callbacks and pushes it to the frontend via SSE; the GitHub check-runs API is used only for reconciliation.
7. When a batch finishes, the platform aggregates metrics: it computes evaluation-level results according to the metric definitions registered per dataset (such as resolve rate), and the result page renders them.

## 5. Functional Design

### 5.1 Accounts and Tenancy

Login uses username and password; passwords are stored with salted hashes, and sessions use signed tokens. The data model follows three layers: organization, workspace, and project, and every business table carries a tenant ID. The single-tenant phase holds exactly one organization record, and adding a tenant requires no schema change.

### 5.2 Dataset Management

Datasets and dataset versions are separate, and versions are immutable; cases store fields compatible with the SWE-bench de facto standard (instance ID, repository, base commit, problem statement, reference patch, test patch, expected-to-pass tests, must-not-regress tests), while dataset-specific fields live in JSONB. The dataset adapter interface handles import, browsing, and the executor-facing view; the dataset detail page shows the case count, expected metrics, and the case list. Adding a new benchmark only requires a new adapter and registered metric definitions; nothing else in the platform changes. Dataset configuration follows five fixed steps:

1. On the dataset page, pick a benchmark type, which is a registered dataset adapter; SWE-bench is provided first.
2. Enter the data source (upload a data file or a dataset address) and a version number, and start the import.
3. The adapter parses and validates the standard fields, cases are bulk-imported per version, and expected metrics are registered along the way.
4. The platform generates the task view of that version per the evaluation task package contract, fixing the environment definition, scoring command, and metric mapping with the version.
5. The dataset detail page immediately shows the case count, expected metrics, and the case list, and the version can be referenced directly when launching an evaluation.

### 5.3 Model and Credential Management

A model configuration records the provider, endpoint URL, and model name, all integrated through the OpenAI-compatible protocol; API keys are stored encrypted and are only decrypted when dispatching tasks. When launching an evaluation, the user can reference an existing credential or enter a new one temporarily; temporary credentials bind only to that evaluation. Tasks sent to runners carry short-lived credentials issued by the platform, never written to the runner machine.

### 5.4 Agent Runtimes and Assets

An agent runtime records its name, version, and launch command template; Claude Code is the first integration, followed by Codex, OpenCode, and pi under the same adapter interface. Agent assets ship with the task as evaluation input, and the runtime adapter maps the required skills, commands, and subagents into the workspace for native loading. The adapter interface has three fixed stages: start with streaming events, collect raw session files, and normalize into the unified trace model.

Skills, commands, and subagents belong to configurable agent asset sources. An asset source binds a Git repository, tenant-level source connection, default ref, scan paths, selection mode, and asset types; selection mode supports all assets under a directory or only explicit paths. A skill is identified by a directory containing `SKILL.md`, while commands and subagents are recognized by the target runtime adapter in their native formats; when recognition is unreliable, the user must specify the type explicitly rather than relying on file extensions.

When launching an evaluation, the user selects an asset source and the ref field is prefilled with the source's default ref; the user may change it to another branch, tag, or commit. After submission, the platform resolves `requested_ref` to `resolved_commit`, verifies that the selected paths and assets exist under the requested permission scope, freezes the asset list, content digest, and runtime prerequisites, and writes an immutable package containing only the selected assets to OSS. Every shard in an evaluation uses the same snapshot; later commits, branch moves, or branch deletion do not change an evaluation that has started, and runners never return to Git during execution.

The asset source records the resolved commit, tree digest, configuration selection, ownership, and auditor; an evaluation references the immutable snapshot rather than a mutable repository ref. Superpowers can be read as a complete asset source rooted at `skills/`. OpenSpec's `skills/` is a skill source, while its commands are generated in the target runtime by `openspec init/update`, so such repositories must declare their runtime prerequisites in the asset source. Adapters perform runtime materialization: Claude Code uses `.claude/` asset directories, while Codex, OpenCode, and pi share `.agents/` skill directories; commands and subagents continue to use each runtime's native format.

### 5.5 Resource Pool

The resource pool registers self-hosted runner metadata and labels; GitHub routes tasks to the Mac mini or Linux machines by runs-on labels. All runners run in ephemeral mode: take one task, execute, deregister, and let a daemon on the machine re-register automatically, so no long-lived credentials or state remain. The Mac mini handles macOS-specific cases with one tart virtual machine per task; Linux machines run Docker sandboxes for container-based benchmarks. Privacy requirements: the repository hosting evaluations must be private, and runner machines store no long-lived credentials. Resource pools are separated from access credentials: credentials are registered as tenant-level resource connections, a pool references a connection and stores only pool parameters, and one connection can back multiple pools; with multi-tenancy, each tenant keeps its own connections, invisible to other tenants. In the single-tenant phase, every connection automatically belongs to the default tenant, the mechanism is identical to multi-tenancy, and enabling multi-tenancy requires no data model migration.

Resource connections and provisioning modes combine under capability constraints rather than as two freely paired dropdowns. A resource connection records its provider, region, and capability list, while each provisioner declares the connection capabilities it requires; the pool creation UI first selects a connection and then enables only compatible provisioning modes, and the backend validates the pair again before calling the provisioner. For example, a GitHub private runner connection can use only self-owned machine registration or runner groups, while Aliyun spot instances must reference an Aliyun connection with ECS creation permission. An incompatible request returns `connection_provider_mismatch`, creates no pool, and never reaches instance creation. Table 8a lists the compatibility relationships.

Table 8a Resource connection and provisioning mode compatibility

| Resource connection | Allowed provisioning modes | Typical incompatible combination |
|---|---|---|
| GitHub private runner | Self-owned machine registration, user-managed runner groups | Spot, pay-as-you-go, or subscription ECS; the connection has no cloud-provider API permission |
| Aliyun ECS | Subscription ECS, pay-as-you-go ECS, spot ECS | Self-owned machine registration or user-managed runner groups; these belong to a GitHub runner connection |
| Aliyun ECI | Elastic Container Instance ECI | ECS spot and self-owned machine registration; these belong to the corresponding connection and provisioner |

### 5.6 Scheduling and Live Progress

The dispatcher shards evaluations into batches and dispatches them concurrently; each evaluation uses a concurrency group to prevent duplicate triggers. The case state machine is queued, running, succeeded, failed, timed out, and canceled. Live progress is based on heartbeats from the in-job script; cases without callbacks past the timeout are marked by the dispatcher and trigger the retry policy. The dispatcher abstracts the execution backend behind one interface; the current implementation is the GitHub Actions backend, and a queue backend can be added later without changing the in-job scripts.

### 5.7 Scoring and Results

Scoring is not reimplemented: SWE-bench cases go to the official harness scoring images, which produce structured reports that the platform parses and stores. Result metrics are registered per dataset: each dataset declares its own metric definitions and presentation form (resolve rate, pass rate, weighted total, and so on). The case table stores raw metric snapshots, and the result page renders the corresponding metric cards and case detail table per dataset.

### 5.8 Session Traces

Session traces use normalized storage as the primary source, with raw files archived as the fallback: adapters map each agent's native sessions (Claude Code JSONL, Codex JSONL plus SQLite, OpenCode SQLite, pi tree-shaped JSONL) into a unified two-level session and span model. Attribute keys follow the OpenTelemetry GenAI and OpenInference conventions, and evaluation-domain custom keys carry an evaluation prefix. The session table expresses the primary agent and sub agent tree through a parent session ID, and the span table records the hierarchy, duration, and token breakdown of messages, tool calls, and thinking steps. The frontend drills down from a case row to the session tree and expands it into a span timeline; raw files are fetched from OSS for replay or audit.

### 5.9 Execution Isolation

Evaluation tasks always run inside containers, and the runner host trusts no task code. Isolation has two layers: the first is the job-level container of the workflow, carried by GitHub Actions' native container configuration, which loads only the in-job script and toolchain and is the only execution entry of a task on the host; the second is the case sandbox container, started from the case's environment definition, where the agent and the code under test run exclusively, with a non-root user, read-only filesystem mounts, and network opened only as needed. Neither layer holds long-lived credentials; task credentials are issued by the platform and discarded after use. When a case container needs Docker capability, the host Docker socket is not mounted (mounting would defeat the isolation); rootless Docker or a separate Docker service container is used instead. The Mac mini has no Linux containers, so its isolation layer uses tart virtual machines, one per task, equivalent to the two-layer container model.

### 5.10 Evaluation Task Package Contract

The platform and the eval-runner repository are decoupled by a generic evaluation task package, so adding a new evaluation dataset requires no code change in eval-runner. The task package always carries four parts: case content, environment definition, scoring command, and metric mapping, laid out as five files: task.toml records the task name and metadata, instruction.md is the task statement, environment/Dockerfile defines the environment, tests/test.sh is the scoring command, and solution/solve.sh is the reference solution used only for self-checks. This layout matches Harbor's task format, so Harbor community adapters can be ported directly later. The in-job script recognizes only the contract, not the dataset, and executes in a fixed order: start the sandbox, inject the case, run the scoring command, parse the result file, and report back. Adding a dataset only requires implementing a dataset adapter on the platform side and registering the scoring script or scoring image as part of the dataset version; only an evolution of the contract itself requires syncing the runner. SWE-bench is the first implementation of this contract: the scoring command is the official scoring image, and the metric mapping is the resolve rate.

### 5.11 Multi-Tenant Resource Permissions

When multi-tenancy is enabled, resources and permissions are managed in four layers. The first is physical isolation: runner groups or repository-level runners restrict each tenant to its own execution machines. The second is logical ownership: resource pool records bind to workspaces, the platform dispatches only to pools the tenant is entitled to use, and shared pools run in a per-tenant allowance mode. The third is fairness: the dispatcher counts a concurrency limit per tenant, and tasks beyond the limit queue up, preventing one tenant from occupying all resources. The fourth is credentials and audit: short-lived credentials are issued per tenant and operator and recorded end to end, so every task launch and execution is traceable. In the single-tenant phase, all four layers degrade to default values, with no schema or code change. Resource connections and pools carry a tenant ID from day one: in the single-tenant phase they all belong to the default tenant, and when multi-tenancy is enabled they are split per tenant, with the onboarding flow and data model unchanged.

### 5.12 Adding Datasets and Metrics

Adding a benchmark splits into two kinds of work: one-time development in the platform repository, and daily operation through the interface. When only a new metric is needed without a new dataset, skip the development part and register the metric definition directly.

One-time development (platform repository, shipped with a release):

1. Implement a dataset adapter that converts an external benchmark into task directories in Harbor's task format, with four fixed methods: parse the source, validate fields, bulk-import, and generate the task view, plus three command-line flags for output directory, item limit, and specific case IDs.
2. Register the metric definition: metric name, computation (such as resolved cases over submitted cases), presentation form (metric card, detail table, or distribution chart), and a definition note.
3. Register the adapter and metric definitions in the dataset type registry; after that, no other part of the platform changes.

Dataset versions are identified by the triple of source, commit or tag, and adapter version, so any version can be reproduced completely. The first release implements only the SWE-bench adapter, with artifacts aligned to Harbor's task format; later benchmarks can be ported from Harbor community adapters while keeping the original project's notice.

Daily operation (interface actions):

1. Create a dataset and version, choose the benchmark type, and fill in the source, commit, and version number.
2. Run the import: the adapter generates task packages into object storage, and the page shows the case count and any validation failures.
3. Spot-check case details and the generated task packages to confirm the statement, environment, and scoring scripts.
4. Choose that version when launching an evaluation; the result page renders automatically with the registered metrics.

### 5.13 Adapter Extension Model

An adapter reads the dataset, applies built-in conversion rules, and emits the unified task package; the dataset repository is referenced read-only and never forked or modified. Conversion rules come in three layers: declarative mapping for plain question-answer benchmarks, where configuration alone suffices; adapter code for benchmark families that need containers and tests, written once and reused; and community adapters that can be ported, with more than sixty available from Harbor (Apache-2.0).

A new benchmark falls into one of three cases by similarity to existing adapters; only the third needs new code, and all of it is additive rather than modificatory:

| Case | Examples | Effort |
|---|---|---|
| Derived sets sharing the same schema | SWE-bench Lite, Verified, Multimodal, Multilingual, SWE-rebench | No code; register a new version (source, commit, adapter version) |
| Same family with field differences | multi-swe-bench and similar | Add a configuration profile, or a thin subclass overriding one method |
| Entirely different shapes | Terminal-Bench, GAIA, OSWorld | Add an adapter package and register it, preferably porting a Harbor adapter; existing adapters and the executor stay untouched |

Existing adapter code changes only when the upstream data format changes or the adapter has a defect; that is maintenance, handled by bumping the adapter version and re-running validation. The extension rule stays constant: the contract layer (five-file task package) is stable, the adapter layer only grows, and the execution layer is unaffected.

## 6. Core Data Model

Table 2 lists the core tables; every business table carries a tenant ID, and the evaluation case table is the hub for session drill-down.

Table 2 Core data model

| Table | Purpose | Key fields |
|---|---|---|
| tenant / user / workspace / project | Multi-tenant skeleton | Tenant ID, role, workspace and project membership |
| credential | Model credentials | Provider, endpoint URL, encrypted key, owning project |
| connection | Resource connection (tenant-level access credential) | Provider, region, capability list, encrypted credential, status, last verification time |
| dataset / dataset_version / case | Datasets and cases | Version, SWE-bench standard fields, custom JSONB |
| metric_definition | Per-dataset metric definitions | Metric name, computation, presentation form |
| agent_runtime | Agent runtime adapters | Name, version, command template, trace format |
| agent_asset_source | Git asset sources | Repository, source connection, default ref, scan paths, selection mode, asset types |
| agent_asset_snapshot / agent_asset | Immutable asset snapshots and assets | Resolved commit, tree digest, OSS address, content hash |
| resource_pool / runner | Resource pool registry | Connection ID, provisioning mode, labels, status, last heartbeat |
| eval_run / eval_case_run | Evaluations and case executions | State machine, metric snapshots, asset snapshot, session ID, timeout |
| session / span | Normalized traces | Parent session, span kind, attribute JSONB, tokens and cost |

## 7. Concurrency Capacity

The concurrency model is simple: one runner executes one task at a time, so the platform concurrency equals the number of online runners. The Mac mini should register 2 to 4 ephemeral runner instances isolated by tart virtual machines, limited by the machine CPU and memory; for higher concurrency, add Linux runners on Aliyun to scale horizontally, and one evaluation batch carries at most 256 cases through a single job matrix. Boundary conditions to note: when no runner is idle, tasks queue on the GitHub side, where delay is unpredictable but no task is lost, and queuing caps at twenty-four hours. When concurrency demand exceeds 50 to 100, or when second-level deterministic scheduling is required, the dispatcher evolves into self-built workers plus a queue by enabling the queue backend, with in-job scripts unchanged.

## 8. Elastic Resource Pool Provisioning

### 8.1 Provisioning Modes

Compute sources for the resource pool are abstracted as pluggable provisioning modes, each implemented by a provisioner that creates and releases execution machines on demand. The first release implements two provisioners: self-owned machine registration and spot instances. Other modes reserve the same extension point: after preparing the resources, a user picks a mode on the resource pool page, enters the resource credentials and capacity limit, and the pool is connected without a platform release. Table 8 compares the modes.

Table 8 Provisioning mode comparison

| Mode | Pricing | Characteristics | Status |
|---|---|---|---|
| Self-owned machine registration | Existing hardware, no cloud resource fee | Register to join and withdraw anytime; fits the Mac mini and office machines | First release |
| Subscription ECS | Subscription price, about a quarter of pay-as-you-go | Stable and never reclaimed; fits baseline capacity | Reserved |
| Pay-as-you-go ECS | Billed per second, release anytime | No reclamation risk; fits short, predictable batches | Reserved |
| Spot ECS instances | Floating at 1 to 3 tenths of pay-as-you-go | May be reclaimed; tasks must be retryable; fits large batches | First release |
| Elastic Container Instance (ECI) | Billed per second, vCPU and memory priced separately | Delivered in seconds, stops after the run, no machine maintenance | Reserved |
| User-managed runner groups | User's own cost | The platform only records labels and heartbeats and does not provision; fits multi-cloud and on-premises | Reserved |

### 8.2 How the Spot Provisioner Works

The spot provisioner watches the dispatch queue: when queued tasks exceed baseline capacity, it creates spot instances automatically, registers them as ephemeral runners, and tags them with the resource pool label. Instances are released when tasks finish or when reclaimed, so capacity scales with load. A spot instance has a one-hour protection period after creation and may then be reclaimed when inventory runs low or the market price exceeds the bid, so interrupted tasks are retried automatically by the dispatcher, and the case-level state machine guarantees that reruns produce no duplicate results. Cost-wise, spot instances run at one to three tenths of pay-as-you-go for the same size; compute fees accrue by actual execution time, and idle time costs nothing.

### 8.3 Provisioner Extension Mechanism

Adding a provisioning mode takes three steps: implement the provisioner interface (connection capability declaration, create, release, and health check), register the mode definition and configuration form in the platform, and users can then prepare resources and connect them to a pool. The connection capability declaration fixes the required provider and capability keys; the API validates them before saving a pool, and the configuration UI enables or disables modes from the same declaration. The dispatcher schedules only by pool labels and capacity and is unaware of provisioning mode differences; exceptions such as instance reclamation and machine dropout are handled uniformly by the retry and re-registration flows. The same mechanism also covers the future execution backend switch: when self-built workers plus a queue land, the provisioner interface stays unchanged and only the registration target of execution machines changes from GitHub runners to platform workers.

### 8.4 Resource Pool Configuration Flow

Resource pool configuration follows two steps: create a connection first, then a pool. A connection is the access credential of a cloud account, tenant-level and reusable; a pool references a connection and stores only pool parameters, and one connection can back multiple pools. The resource pool page exposes compatible provisioning modes only after a connection is selected: a GitHub private runner connection shows self-owned machine registration and runner groups while disabling cloud modes such as spot with an explanation that an Aliyun ECS connection is required; an Aliyun ECS connection enables only subscription, pay-as-you-go, and spot modes. Take the spot elastic pool as an example: the user fills in the Alibaba Cloud key on the resource connection page and the platform performs read-only verification; then the user selects that connection and the spot mode on the resource pool page and fills in the region, vSwitch, instance type, per-instance capacity, and pool limit, and the provisioner takes over. When queued tasks exceed baseline capacity, it creates instances and registers them as ephemeral runners tagged with the pool; instances are released when tasks finish or when reclaimed, and the pool page shows capacity, online execution machines, and today's spend in real time. The self-owned machine mode is simpler: run the one-line registration script provided by the platform on the machine, and it registers as an ephemeral runner tagged with the pool. The difference between the two modes lives only inside the provisioner and is fully transparent to scheduling and evaluation.

### 8.5 Adding a Resource Pool (Aliyun Spot Pool Example)

Adding a pool has two stages: create a resource connection, then create the pool. The first integration takes about fifteen minutes; afterwards the connection is reused and adding another pool needs only pool parameters. In the single-tenant phase connections automatically belong to the default tenant; when multi-tenancy is enabled, each tenant creates pools under its own visible connections, with the flow unchanged.

Resource connection (one time, tenant-level):

1. Activate ECS, confirm the account has spot instance permissions and available capacity, and choose the region and availability zone.
2. Create an access key granted only the permissions to create, release, and query spot instances.
3. Create a connection on the resource connection page with the provider, region, and key; the platform performs read-only verification (list regions, check capacity, inspect permission scope), and the connection becomes usable once it passes.
4. Prepare the vSwitch and security group, allowing only the necessary inbound ports and outbound traffic to the platform callback address, GitHub, and software sources, and build a base image with the runner and evaluation environment preinstalled.

Resource pool (may reuse the same connection):

1. Create a resource pool on the resource pool page and select the connection just created plus the spot instance provisioning mode.
2. Fill in the region, availability zone, vSwitch, instance type, system disk size, per-instance capacity, pool limit, and bid ceiling.
3. After saving, the provisioner takes over: it generates the registration configuration, prepares the image, and the pool status becomes ready.

Verification and observation:

1. Click test provisioning; the provisioner creates one instance and registers it as an ephemeral runner, shows it online, then releases it automatically.
2. Launch a small evaluation of five to ten cases and confirm tasks land on this pool and results come back normally.
3. Day to day, watch capacity, online execution machines, and today's spend on the pool page; interrupted instances trigger automatic case retries with no manual intervention.

## 9. Aliyun Deployment and Purchase List

Platform services run as Docker Compose on an Aliyun ECS instance; the database is RDS PostgreSQL, object storage is OSS, and runner machines are added as evaluation scale grows. Table 3 lists the purchase list.

Table 3 Purchase list

| Resource | Suggested size | Purpose |
|---|---|---|
| ECS | 2 vCPU 4 GiB to start, pay-as-you-go upgrade | Platform containers: web, API, dispatcher, Redis |
| RDS PostgreSQL | Entry-level high availability | Business and trace database |
| OSS | Standard | Raw sessions, evaluation artifacts, image cache |
| Cloud Redis | Or self-hosted on ECS first | Cache, locks, push buffer |
| Second ECS (on demand) | Linux runner host | Evaluation execution and sandbox host |

The CI/CD pipeline is defined in GitHub Actions and runs automatically after a merge into main: step one runs tests and documentation gates; step two builds platform images and pushes them to Aliyun Container Registry; step three runs the migration tool (Alembic) upgrade scripts against the database, and a failed migration blocks all later steps; step four rolls the Compose services on the ECS via SSH with a health check, and rolls back to the previous image on failure. Building the evaluation base images and scoring images is handled by a separate workflow in the eval-runner repository.

### 9.2 Budget Reference

The prices below are reference values from public quotes around late 2025 to early 2026; the actual purchase page prevails. Platform spending splits into fixed and on-demand categories: the fixed part keeps the platform running, and the on-demand part is incurred only during evaluation execution. Table 9 shows the monthly budget for two configurations.

Table 9 Budget reference (monthly equivalent)

| Item | Trial tier (MVP) | Recommended tier (growth phase) |
|---|---|---|
| Platform ECS | 2 vCPU 2 GiB promotional machine about ¥3~8/month, or regular 2 vCPU 4 GiB economy type e subscription about ¥37/month | 2 vCPU 4 GiB general-purpose u1 with 5M bandwidth, about ¥100/month |
| Database | Self-hosted PostgreSQL on the ECS first, ¥0 | RDS PostgreSQL basic edition high availability, about ¥300~500/month |
| Redis | Self-hosted on the platform ECS, ¥0 | Cloud Redis 1 GiB primary-replica, about ¥100/month |
| Object storage OSS | 100 GiB storage plan about ¥8/month, intranet traffic free with machines in the same region | 500 GiB storage plan about ¥41/month, intranet traffic free |
| Evaluation compute | Own Mac mini, no cost | One subscription host as baseline, spot instances scale on demand (about ¥0.03~0.08 per machine hour) |
| Monthly total (excluding evaluation compute) | About ¥15~100 depending on promotions | About ¥550~750 |

Cost-saving essentials: always use subscription for long-running resources, about a quarter of pay-as-you-go; deploy the platform, execution machines, and object storage in the same region so intranet traffic is free, and charge public bandwidth by traffic (about ¥0.8/GB, a few to tens of yuan per month at low volume); evaluation compute runs on the spot provisioner, started on demand and costing nothing when idle.

Self-hosting and migration in the trial tier: the trial tier runs PostgreSQL and Redis as containers on the same platform ECS, orchestrated in the same Compose file as the business services, saving the managed database cost. The trade-off is no high availability: configure daily disk snapshots and add a scheduled backup job in Compose that uploads database exports to object storage. Migrating to a managed database only requires changing the connection string; migration scripts and the schema stay unchanged, and the switch completes in a low-traffic window. Move to the managed databases of the recommended tier when evaluation data grows to hundreds of thousands of rows, when the platform serves many users at once, or when self-managed backups are no longer desired.

Cheaper options: for the platform host, watch the promotions of Simple Application Server and economy type e (2 vCPU 2 GiB as low as ¥38~99 per year), enough to carry the whole trial tier; for the database, consider RDS PostgreSQL Serverless, billed per second with automatic start-stop, charging storage only when idle, which fits the bursty load of an evaluation platform; for Redis, the platform's usage is tiny while the cheapest managed tier still costs about ¥60~100 per month, so self-hosting is recommended for both the trial tier and the early growth phase, with negligible memory overhead.

### 9.3 Evaluation Cost Model

The cost of one evaluation consists of model fees and compute fees, with model fees usually accounting for more than ninety percent. Model fees are billed per token and compute fees per machine hour; both grow linearly with the number of cases and per-case complexity, while parallelism only shortens waiting time and does not change total cost.

Model fees are illustrated with deepseek-v4.1-flash, priced by peak and off-peak periods: off-peak cache-hit input ¥0.02 per million tokens, cache-miss input ¥1 per million tokens, and output ¥4 per million tokens, doubled during peak hours. The token volume per coding-agent case depends on case complexity and is estimated in three tiers: light, standard, and heavy, assuming a seventy percent cache hit rate; Table 10 shows per-case model fees.

Table 10 Per-case model fees (deepseek-v4.1-flash)

| Tier | Input tokens | Output tokens | Off-peak fee | Peak fee |
|---|---|---|---|---|
| Light | 300K | 15K | About ¥0.15 | About ¥0.31 |
| Standard | 500K | 30K | About ¥0.28 | About ¥0.56 |
| Heavy | 1.2M | 100K | About ¥0.78 | About ¥1.56 |

Compute fees are billed per machine hour: a 4 vCPU 8 GiB spot instance at a planned rate of ¥0.12/hour, with about 15 minutes of total time per case covering agent reasoning, scoring tests, and amortized environment preparation, plus a ten percent retry waste factor, yields about ¥0.033 per case.

Dataset-level total costs are estimated at the standard tier and shown in Table 11. Scheduling bulk evaluations off-peak halves the model fees.

Table 11 Dataset-level evaluation cost (standard tier, off-peak)

| Dataset | Cases | Model fees | Compute fees | Total |
|---|---|---|---|---|
| SWE-bench Lite | 300 | About ¥84 | About ¥10 | About ¥94 |
| SWE-bench Verified | 500 | About ¥140 | About ¥17 | About ¥157 |
| Full SWE-bench | 2,294 | About ¥642 | About ¥76 | About ¥718 |

Cost reduction measures in order of effect: schedule bulk evaluations off-peak to halve model fees; keep prompt prefixes stable to raise the cache hit rate, cutting the standard-tier per-case cost from about ¥0.28 to about ¥0.22; choose the dataset tier by purpose, with Lite for daily regression, Verified for release acceptance, and the full set only for public leaderboards. These figures form an estimation model; after the platform runs, actual tokens and machine hours from evaluation records prevail.

## 10. Target-State Deployment View

### 10.1 Services and Resources

Table 5 lists the services and resources the platform needs in the target state. The minimum configuration runs the single-tenant phase, and resources scale horizontally with evaluation volume.

Table 5 Services and resources

| Owner | Service or resource | Purpose |
|---|---|---|
| Aliyun ECS (platform host) | Web frontend, API service, dispatcher, trace importer | Four container services cover the UI, business APIs, evaluation scheduling, and session normalization |
| Aliyun RDS PostgreSQL | Business and trace databases | All business data and normalized session traces |
| Aliyun Redis | Cache and locks | Login session cache, dispatch dedup, progress push buffer |
| Aliyun OSS | Object storage | Raw session files, evaluation artifacts, image cache layers |
| Aliyun ACR | Image registry | Distribution of platform images and evaluation base images |
| Aliyun ECS (execution host, add on demand) | Linux runners and Docker sandboxes | Runs container-based evaluation cases |
| Own Mac mini | macOS runner and tart virtual machines | Runs macOS-specific cases, one virtual machine per task |
| GitHub private repositories | Platform repository and eval-runner repository | Platform source code and CI/CD; the eval-runner repository holds the generic workflow and image builds |
| GitHub Actions | Hosted scheduling channel | Task queuing, label-based routing, execution reconciliation |

### 10.2 Service Call Flow

The target state has ten stable call chains; daily operation only requires understanding this table: browser-API interaction goes through frontend requests, evaluation execution goes through the scheduling channel, large files go only to object storage, and progress pushes go through the server-side real-time channel.

Table 6 Service call flow

| No. | Caller | Receiver | Channel | Content | When |
|---|---|---|---|---|---|
| 1 | Browser | API service | HTTPS API | Page requests and form data | Every user action |
| 2 | API service, dispatcher | PostgreSQL, Redis | Database and cache protocols | Business reads and writes, cache, task locks | Every request and every dispatch |
| 3 | Dispatcher | GitHub | Dispatch API (repository_dispatch) | Evaluation ID, case batch, callback address | Evaluation start and redispatch |
| 4 | GitHub | Runner machines | Task assignment | Case definition and short-lived credentials | When an eligible idle runner exists |
| 5 | In-job script | Agent sandbox | Local commands and streaming output | Prompt, agent asset snapshot, model data stream | While a case executes |
| 6 | In-job script | API service | HTTPS callback | Heartbeat, case status, metric summary | During execution and on case completion |
| 7 | In-job script | OSS | Upload API | Scoring report, artifact bundle, raw session files | On case completion |
| 8 | Trace importer | PostgreSQL, OSS | Write and read | Normalized sessions and spans, raw file retention | After receiving the completion callback |
| 9 | API service | Browser | Server-sent events (SSE) | Progress and status changes | On status change |
| 10 | Browser | OSS | Time-limited download URL | Raw session file download | When viewing raw trace data |

## 11. Operation Scenarios and Data Flow

The target state has six frequent operation scenarios; each is described below by who operates and how data flows.

### 11.1 Configuration Management Scenario

After logging in, an administrator enters model credentials, imports datasets, and registers resource pools. The browser submits the username and password to the API service, which issues a token after verification; subsequent requests carry the token. Model credentials are stored encrypted in the database, and pages only show masked values. When importing a dataset, the admin uploads a data file or enters a dataset address; the dataset adapter parses the content into standard fields, cases are bulk-imported per version, and expected metrics are registered along the way.

### 11.2 Launching an Evaluation Scenario

An evaluation owner selects a dataset version, agent runtime, agent asset source, Git ref, model credential, and resource pool on the launch page. After submission, the API service resolves the ref and commit, generates an immutable asset snapshot, creates an evaluation, and writes cases into the case table in batches; the dispatcher calls the dispatch API batch by batch, and GitHub assigns tasks to runners with the requested labels. The script on the runner pulls the task package (case definition, short-lived credentials, asset snapshot) and drives the agent inside the sandbox until a patch is produced.

### 11.3 Watching Progress Scenario

During execution, the runner script calls back to the platform on every heartbeat, reporting case status and summary metrics. The API service updates the case table, publishes the change through Redis, and pushes it to the browser via the server-side real-time channel, so the frontend progress bar and case statuses refresh live. Only status and summaries travel in this scenario; large files such as scoring reports and artifact bundles bypass the API and upload directly to object storage.

### 11.4 Viewing Results Scenario

When a batch finishes, the dispatcher aggregates evaluation-level results according to the metric definitions registered per dataset, and the result page renders metric cards and a case detail table. Case-level metrics come from parsed scoring reports and are stored as metric snapshots in the case table, keeping them consistent with the metric definition at evaluation time.

### 11.5 Trace Replay Scenario

A user clicks a case in the detail table, and the API service looks up the session tree by session ID (parent-child relations map to the primary agent and sub agents); the frontend expands it into a span timeline. When raw data needs verification, the API service requests a time-limited download URL from object storage, and the browser downloads the raw session file directly with that URL.

### 11.6 Deployment and Operations Scenario

After code merges into main, the pipeline runs automatically: tests and documentation gates, image builds pushed to the registry, migration scripts executed against the database (failure blocks the rest), and a rolling update of the platform containers over SSH with a health check. A daemon on each runner machine reports heartbeats to the platform regularly, and the resource pool page shows online status accordingly; runners that go offline enter the re-registration flow automatically.

## 12. Risks and Evolution

Table 4 lists the main risks and countermeasures.

Table 4 Risks and evolution

| Risk | Impact | Countermeasure |
|---|---|---|
| GitHub changes pricing for self-hosted runners on private repositories | Resource pool cost increases | Track official announcements; the dispatcher backend abstraction allows switching to a self-built queue |
| Untrusted code executes on runners | Host and credential leakage | Everything runs in sandboxes, runners use ephemeral mode, and no long-lived credentials are stored |
| A single batch exceeds 256 cases | Hits the matrix limit | The dispatcher splits it into multiple batches automatically |
| Agent output format changes after upgrades | Trace parsing fails | Adapters are versioned, and parsing falls back to raw file display |
| Concurrency exceeds runner capacity | Queueing delay grows | Add Linux runners; switch to the queue backend at the evolution threshold |
