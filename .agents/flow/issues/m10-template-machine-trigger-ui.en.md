# Template API modal and boundary card document machine triggers

English | [中文](m10-template-machine-trigger-ui.md)

```yaml flow
kind: issue
milestone: m10
priority: P1
status: ready
scope:
  - .agents/flow/issues/m10-template-machine-trigger-ui.md
  - .agents/flow/issues/m10-template-machine-trigger-ui.en.md
  - .agents/flow/issues/m10-template-machine-trigger-ui.i18n.yaml
  - prototype/app.js
  - prototype/index.html
adr: []
github:
  number: 433
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/433
```

## Background

#429 has landed: template launch accepts service tokens and `asset_override` candidate injection, status/results echo `asset` and `lineage`, and `/api/v1/service-tokens` exists. But the prototype's "模板接口" modal still says the fixed fields cannot be overridden at launch, its curl examples only cover the console session cookie, and the service tokens are absent; the template detail "边界" card likewise claims launch requests cannot override fixed fields. An integrator following the prototype would build against a stale contract. Source: PR #431 review §6.8.

## Goals

- Update the "模板接口" modal to the #429 contract: the auth row distinguishes console session from service token, launch gains a Bearer + `asset_override`/`lineage` example plus the 202 `asset` echo, and status/results document `eval:read` reads with `asset`/`lineage` echoes.
- Add a `/api/v1/service-tokens` block: create/list/revoke, plaintext returned exactly once at creation, the 401/403/429 error mapping, and `svc:<name>` audit.
- Reword the "边界" card and the self-evolution banner so the console one-click launch cannot override while machine triggers (service tokens) may carry candidate overrides, with reporting inherited from the template and surfaced through `warnings`.

## Acceptance

- The prototype change only touches `prototype/app.js` and the rebuilt `prototype/index.html`; `node --check prototype/app.js` passes and the rebuilt page renders the "模板接口" modal and the template detail correctly.
- This issue description contains before/after prototype screenshots of the API modal and the template detail (boundary card included).
- `npm run verify:flow`, `npm run verify:flow-online`, and PR CI pass; the implementation-side (`web/`) sync is a separate issue outside this issue's scope.
