# [m8] Validate image digests when saving templates and show launch blockers with fixes

English | [中文](m8-template-save-preflight.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - prototype/**
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - .agents/flow/issues/m8-node-download-network-retry.md
  - .agents/flow/issues/m8-node-download-network-retry.en.md
  - .agents/flow/issues/m8-node-download-network-retry.i18n.yaml
  - .agents/flow/issues/m8-template-save-preflight.md
  - .agents/flow/issues/m8-template-save-preflight.en.md
  - .agents/flow/issues/m8-template-save-preflight.i18n.yaml
adr: []
github:
  number: 398
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/398
```

## Background

Production template launches failed with "pre-launch consistency check failed". The root cause was that dataset version `v2026.11-m825-prod` and the platform default both lacked execution/scoring image digests, yet the template itself saved successfully and users only saw a summary error at launch. Image-digest resolution is deterministic (launch override → dataset version → platform default), so a template that can never launch is detectable at save time; at launch the backend already returns `details.blockers`, but the UI only toasts the summary error.

## Goals

- Before saving a template, the prototype shows the image-digest dependency: when both the dataset version and platform default are empty, it lists the missing execution/scoring digests, their fields, and remediation, and blocks the save.
- When a launch is blocked, the prototype lists each blocker's code, reason, field, and remediation, and states that no evaluation was created; the template list and detail page mark the template as needing an image digest.
- Design section 5.14 records the deterministic boundary of save-time validation and the launch-blocker display contract.
- Before/after screenshots use the same viewport and theme and are attached to issue #398 and the prototype PR.

## Non-goals

- This prototype PR does not change production `web/` or `backend/`; production implementation follows the acceptance items in issue #398.
- The `prepare`/`create` execution path, launch API contract, and image-digest resolution order stay unchanged.
- Save-time validation does not cover asset loading, capacity, or model credentials, which depend on live state.

## Acceptance

- Selecting "production slice" v2026.11-m825-prod shows two missing image digests and remediation before save; clicking save is rejected without losing the entered configuration.
- A dataset version with both digests ready shows as saveable and writes the template normally.
- Launching "生产验收 · 6×SWE-bench" warns about the blocker in the confirmation modal; after confirming, the modal lists each blocker's reason, field, and remediation and states that no evaluation was created.
- The design document and prototype README are synchronized; `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs`, and `node prototype/build-standalone.mjs` pass, and PR CI is green.

## References

- Issue: https://github.com/Shadow-Azure/ai-eval-platform/issues/398
- Design: [docs/design/eval-platform-architecture.en.md](../../../docs/design/eval-platform-architecture.en.md)
