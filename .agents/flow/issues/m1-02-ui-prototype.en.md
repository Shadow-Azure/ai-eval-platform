# Design the evaluation platform UI prototype

English | [中文](m1-02-ui-prototype.md)

```yaml flow
kind: issue
milestone: m1
priority: P0
status: done
scope:
  - .agents/flow/issues/m1-02-ui-prototype.*
  - docs/design/eval-platform-architecture.*
  - scripts/doc-budgets.manifest.json
  - prototype/**
  - plans/m1-02-fullstack-design/**
  - reports/**
adr: []
github:
  number: 6
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/6
```

## Background

The finalized architecture covers platform capabilities, data flows, and technology choices, but it does not yet define a directly reviewable page structure, interaction path, or state presentation. Before implementation, the team needs a shared UI baseline so dataset import, evaluation launch, live observation, and trace review do not evolve independently.

## Goals

- Cover the core flows for login, datasets and import, model credentials, resource connections and pools, evaluation launch, progress and results, and session trace review.
- Define the global information architecture, navigation, page layouts, form interactions, and state feedback while staying consistent with React and Ant Design.
- Deliver an interactive prototype with mock data that can be opened or run locally and traverses the main paths end to end.
- Record mappings from pages to architecture requirements, data entities, and future APIs, along with interaction decisions that still need confirmation.

## Acceptance

- `prototype/` contains run instructions and an entry point, and every main flow can be viewed without a backend.
- All seven core flows are clickable and cover the required empty, loading, failure, no-permission, partial-success, and destructive-action confirmation states.
- Credential masking, live evaluation progress, result metrics, the main/sub-agent session tree, and span details are presented clearly.
- The design notes include a page-state matrix, flow mappings, and open questions, with no P0 interaction issue blocking implementation after review.
