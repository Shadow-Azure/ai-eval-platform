# Register datasets and import SWE-bench slices with task bundles

English | [中文](m3-01-swebench-dataset-import.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
adr: []
github:
  number: 23
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/23
```

## Background

M3 evaluates real cases, so datasets must first be imported as immutable versions with task bundles that separate agent-visible files from scoring-only files. The current schema only contains evaluation run tables, without datasets, versions, cases, metric definitions or an import flow.

## Goals

- Add dataset, dataset_version, dataset_case, dataset_import, metric_definition and version-metric tables with migrations.
- Implement a DatasetAdapter registry and a SWE-bench adapter covering source validation, streaming import, field mapping and metric definitions.
- Generate five-file task bundles and manifests into object storage, separating agent-visible files from scoring-only files.
- Expose APIs for datasets, versions, cases, metrics and import progress, with a re-entrant import worker that tolerates partial failure.

## Acceptance

- A SWE-bench Verified slice imports as one version, the case count matches the source, and repeated imports do not create duplicate versions.
- Invalid sources and partial failures produce explicit error reports while valid cases are still stored.
- The task bundle manifest records a digest and visibility for every file, and the agent bundle excludes reference and test patches.
- Case pagination, filtering and metric definition queries are covered by automated tests.
