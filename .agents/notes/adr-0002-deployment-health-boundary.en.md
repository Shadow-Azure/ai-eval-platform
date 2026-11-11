# ADR 0002 · Health-information boundary for the delivery pipeline

English | [中文](adr-0002-deployment-health-boundary.md)

- Status: Accepted
- Date: 2026-11-11
- Related issue: #146 (M5-18 delivery pipeline page with controlled rollback)
- Decision maker: the implementer of this issue, based on `plans/m1-02-fullstack-design/02-prototype-and-interactions.md`, `09-frontend-implementation.md` and `10-deployment-testing-and-review.md`

## Context

M5-18 lands the prototype's "delivery pipeline" page at `/deployments`, including an environment component health summary. The platform already has an administrator-only `/operations` "operations status" page showing database capacity, table row counts, Redis memory, object-storage bucket, Runner and model-gateway diagnostics. If both pages showed full health information, one of two problems would follow: a page visible to ordinary members would have to become administrator-only, or the same diagnostic data would be duplicated with slowly diverging definitions.

## Decision

- `/deployments` serves every signed-in member and shows only the **component health summary the deployment needs**: status and a one-line summary for the control-plane API, PostgreSQL, Redis, object storage and the dispatch worker; it returns no latency, capacity, bucket name, table row counts or credential material.
- `/operations` remains platform-administrator-only as the **detailed diagnostic surface**: database size/connections/table inventory, Redis memory and clients, object-storage bucket probe, Runner and model-gateway details, and per-service latency; it offers no deployment rollback entry.
- Deployment records, current/previous versions, the Alembic revision comparison, the five-stage timeline and controlled rollback belong only to `/deployments`; `/operations` does not repeat deployment history.
- Both pages may show whether the database is healthy, at different granularity: the deployment page answers "are the components this deployment depends on healthy", the operations page answers "are capacity, resources and diagnostic details healthy".
- Rollback exists only on `/deployments` and requires all of: platform administrator, an explicit second confirmation, the target being the previous successful version, and both the current and target migration revisions equal to the live database revision; the API writes an audit record and hands the request to the deploy workflow through `repository_dispatch`. The API itself never operates the host directly and never runs a database downgrade.

## Impact

- `backend/app/api/deployments.py` and its session-level dependency provide the compact health summary; `backend/app/api/operations.py` keeps its administrator dependency and detailed payload unchanged.
- `web/src/pages/DeploymentsPage.tsx` shows the environment summary and rollback; `OperationsPage` keeps capacity and diagnostics.
- The new deployment read endpoint is open to ordinary members but grants no write permission; the rollback write endpoint stays protected by `require_admin`.
- A failed health probe only marks that component unhealthy; it never blocks reading deployment history or migration revisions.

## Rejected alternatives

- **Reuse the `/operations` response directly**: that endpoint requires an administrator and returns capacity, table inventory and infrastructure details; exposing it to ordinary members widens the privilege surface, and trimming it would couple both pages to one DTO.
- **Keep health only on `/operations`**: this contradicts the M5-18 acceptance criteria and the prototype, and ordinary members could not tell whether the components behind a deployment are healthy.
- **Show full health information on both pages**: duplicated probes and duplicated presentation let the definitions drift and risk exposing diagnostic information to ordinary members.
- **Have the API SSH into ECS to run the rollback**: the control plane should not hold a host-operation channel; reusing the deploy workflow's SSH and recording path keeps auditing and host operation responsibilities clear.
