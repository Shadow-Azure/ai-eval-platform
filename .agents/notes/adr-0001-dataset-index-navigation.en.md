# ADR 0001 · Datasets belong to the "Evaluation" navigation group

English | [中文](adr-0001-dataset-index-navigation.md)

- Status: Accepted
- Date: 2026-11-11
- Related issue: #141 (M5-13 dataset index adapter cards with filters and pagination)
- Decision maker: the implementer of this issue, based on `plans/m1-02-fullstack-design/02-prototype-and-interactions.md`

## Context

The prototype and frontend design treat datasets as evaluation assets: §1 of `plans/m1-02-fullstack-design/02-prototype-and-interactions.md` places "Datasets" under the "Evaluation" group together with evaluation tasks, while "Configuration" contains only Agent assets, model credentials and resource pools. The 2026-11-11 UI-versus-prototype comparison found that the live app had moved "Datasets" into "Configuration", diverging from the design baseline and leaving the "Evaluation" group without an asset entry.

## Decision

Restore the design baseline: datasets return to the "Evaluation" group alongside evaluation tasks and launch evaluation; "Configuration" keeps only Agent assets, model credentials and resource pools. The sidebar and page breadcrumbs use the same group name so navigation and in-page hierarchy do not contradict each other.

The dataset index page is still a configuration-style entry (importing new versions, viewing adapter specs), but its information-architecture ownership is "evaluation asset"; this changes no API or permission boundary.

## Impact

- The navigation groups and breadcrumbs in `web/src/app/nav.ts` become "Evaluation / Datasets", "Evaluation / Import dataset" and "Evaluation / Dataset detail".
- The dataset index header stops using `Configuration · Datasets` and follows the prototype's dataset-index wording.
- No permissions, routes or backend contracts are added; only navigation ownership changes.
- New dataset types still extend through the backend adapter registry, and index cards read registry metadata.

## Rejected alternatives

- **Keep the "Configuration" group**: this conflicts with the finalized design and mixes the evaluation asset entry with infrastructure configuration such as models and resources; a real product change would need a new design-document revision rather than an implementation-side default.
- **Show the route in both groups**: duplicating the same route in two groups adds navigation noise and does not match the prototype's single ownership.
