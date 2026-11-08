# Architecture Map

English | [中文](architecture.md)

The repository is in the execution-foundation stage of an AI evaluation platform. M2 freezes the Runner task protocol and establishes the control plane, database, and delivery pipeline; M3 starts adding the dataset registry, model gateway, runtime adapters, and scoring.

## Components

| Area | Responsibility | Boundary |
|---|---|---|
| `docs/` | Current facts, development method, and bilingual contract | No roadmaps or decision debates |
| `.agents/flow/` | roadmap, milestone, issue state machine, and acceptance boundaries | Stores flow state only, not implementation details |
| `.agents/skills/` | Task skills and templates synchronized from StudyWiki | Outside bilingual pairing |
| `scripts/` | Mechanical documentation and workflow gates | Not product runtime |
| `.github/workflows/` | CI decisions for docs and flow | No external publishing |
| `backend/` | Control-plane API, data models, migrations, and worker | Not runner-side execution |
| `web/` | React console shell and product pages | No server-side business rules |
| `runner-contract/` | Versioned Runner dispatch, manifest, heartbeat, and callback schemas | Contract only, not platform runtime |
| `runner/` | Reusable workflow template, synthetic task, and local contract tests | No dataset- or agent-specific branches |

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

## Runner contract

M2 decouples evaluation execution from datasets and agents: the platform sends only eval_run, shard, attempt, manifest URL, and digest through repository_dispatch; the runner verifies the token, manifest, and digest before execution, then reports heartbeats, upload manifests, and completion or failure callbacks. `runner-contract/` holds the versioned schemas, while `runner/` holds the reusable workflow template, deterministic synthetic task, and local contract tests; real OSS, OIDC, and platform callbacks arrive with later issues.

## Dataset registry

The `backend/` dataset module stores datasets, immutable versions, cases, and metric definitions in PostgreSQL, and writes five-file task bundles plus manifests to object storage. The `DatasetAdapter` registry starts with SWE-bench: imports validate JSONL row by row, valid cases join the version while invalid rows collect in an error report, and bundles separate agent-visible files from scoring-only files. APIs expose datasets, versions, cases, metrics, and import progress.

## Model gateway

Runners call `/model-gateway/v1` with a short-lived token; the gateway verifies the tenant, model configuration, allowed models, attempt and budget, decrypts the provider key and forwards through OpenAI- or Anthropic-compatible routes, and records token usage and cost per attempt. Provider errors are normalized into stable codes.

## Scoring and results

The `backend/` scoring module parses official SWE-bench reports, stores per-test F2P/P2P results in `test_case_result`, and produces running and final `metric_result` rows from dataset metric definitions. The `resolved_rate` denominator is always the selected case total.

## Extension points

- When the product runtime and evaluation executor exist, add their components, data flows, and external dependencies here instead of debating design in README.
- Development tasks proceed through the roadmap, milestones, and issues under `.agents/flow/`; that directory is authoritative for flow state and change scopes.
- Add platform-specific checks as independent leaves under `scripts/`, combine them in `run-gates`, and wire them explicitly into CI.
- If documentation volume grows, reorganize layers and budgets before considering a site projection; this repository currently has no VitePress or other generated site.
