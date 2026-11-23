# [m10] Evaluation templates cannot configure bad case reporting

English | [中文](m8-eval-template-badcase-reporting.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - backend/app/**
  - backend/migrations/**
  - web/src/**
  - prototype/**
  - tests/**
  - docs/**
  - plans/**
  - scripts/**
  - .agents/flow/**
adr: []
github:
  number: 382
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/382
```

## Background

M10 evaluation templates save a fixed evaluation combination as a reusable object and skip configuration questions at one-click launch. M9 already lets the launch wizard configure optional self-evolution reporting (platform base URL, workspace ID, whk_ token) and reports unresolved cases to harness-evolution when a run reaches a terminal state (#266, #341). The template entity does not carry that configuration:

- Production `eval_template` has no `self_evolution_*` columns and the template create/update APIs have no self-evolution reporting field; `launch_template` calls `build_preparation` without `self_evolution`.
- The web template save payload `buildTemplatePayload` only submits dataset/asset/model/pool/execution/prompt suffix, so the reporting target entered in wizard step 3 is dropped; template details and the one-click launch modal never show reporting configuration.
- Consequence: after configuring reporting in the wizard and saving a template, an evaluation launched from that template does not report bad cases; a user cannot save "hy4-flash + fixed + OpenSpec-cn + SWE-bench 6 cases + reporting" as a reusable template.
- The prototype has the same gap: the M10 prototype wizard step 3 has a reporting section, but `createTemplateFromWizard()` does not copy `selfEvolution`, and the template details/launch modal have no reporting row. The problem originates in the prototype design, not only in the production implementation.

Production evidence: template `49a201b3-87f6-41a8-a89f-cf124c2bef81` (hy4-flash / fixed-linux / OpenSpec-cn / 6 SWE-bench cases / 120 minutes) exists, but its details page has no self-evolution reporting configuration; the same configuration's historical run `5f5d2f48-4ef1-4bb4-af93-453d1a2d1fe7` pushed 3 bad cases successfully (HTTP 201) with run-level reporting. Comparison screenshots are in the Evidence section at the end of this issue.

## Goals

- Add self-evolution reporting configuration to evaluation templates, mirroring the run-level shape: `enabled`, `base_url`, `workspace_id`, `token`; the `token` is encrypted at rest and never returned by any response.
- When the launch wizard saves a template, persist the step 3 reporting configuration with the template; show the reporting target (base URL/workspace, never the token) in template details, the one-click launch confirmation, and the list summary.
- Pass the reporting configuration from template launch into the existing prepare/create chain so a run launched from a template reports bad cases on terminal state through the #266/#341 mechanism.
- Accept and validate the configuration in the template create/update APIs: when `enabled=true` all three fields are required, `base_url` is http(s), and `token` starts with `whk_`; when `enabled=false` all three fields must be empty; updates keep omitted fields and an explicit disable clears the ciphertext.
- Fix the prototype first (save, details, launch confirmation, list display); implement production web and backend to the same contract.
- Attach before/after screenshots (production, prototype, fixed prototype) to the issue and PR for review.

## Non-goals

- Do not change the evaluation execution, scheduling, scoring, or trace pipeline; reporting keeps using the existing outbox and backoff.
- Do not allow field overrides in the template launch request; reporting is saved with the template, and launch only confirms cost and capacity.
- Do not introduce a template-specific reporting retry policy; keep the run-level 60/300/900/3600 second backoff and terminal-failure semantics.
- Production implementation: PR #384; prototype: PR #383.

## Acceptance

- Prototype: after enabling reporting in the wizard and saving a template, template details and the one-click launch modal show "self-evolution reporting: enabled · host · workspace ws" without the token; disabled shows "disabled"; list rows show a reporting indicator; before/after screenshots are attached to this issue and the PR.
- Production: `eval_template` persists the reporting configuration; create/update/launch APIs validate and pass it through by the rules above; a run launched from a template creates `self_evolution_report` rows on terminal state and POSTs successfully (200/201), with idempotent replays.
- Production web: template details/launch confirmation show the reporting target; saving a template from the wizard keeps the step 3 configuration; token input is masked and never enters persistent browser drafts.
- Tests: template create/update validation, enable/disable clearing, launch pass-through, token non-disclosure, and default-disabled existing templates are covered; CI is green.
- Docs: architecture/design docs and the prototype README describe the template-level reporting contract; screenshot evidence uses the same viewport and theme.

## References

- PR #266 (self-evolution reporting), PR #341 (session addressing).
- Issue #378 (evaluation templates).
- Production template `49a201b3-87f6-41a8-a89f-cf124c2bef81`; same-configuration historical run `5f5d2f48-4ef1-4bb4-af93-453d1a2d1fe7`.
- Design: `docs/design/eval-platform-architecture.md`, `plans/m1-02-fullstack-design/`.
- Related PR: #383 (prototype), #384 (production implementation).

## Evidence

Same viewport 1600×1000 and light theme; all captures come from the real application/prototype.

### Production (current implementation)

| Template details (no self-evolution reporting) | One-click launch (fixed config without reporting) | Launch wizard step 3 (has reporting config) |
|---|---|---|
| ![Production template details](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/prod-template-detail.png) | ![Production one-click launch](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/prod-template-launch.png) | ![Production launch wizard step 3](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/prod-wizard-step3-reporting.png) |

Conclusion: the production template saves dataset/assets/model/pool/execution, but neither its details nor its one-click launch shows self-evolution reporting; the same reporting configuration still exists in launch wizard step 3, so the capability works at run level but is missing at template level.

### Prototype before

| Template details (no self-evolution reporting) | One-click launch (fixed config without reporting) | Step 4 (has reporting config) |
|---|---|---|
| ![Prototype before template details](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-before-template-detail.png) | ![Prototype before one-click launch](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-before-template-launch.png) | ![Prototype before step 4](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-before-wizard-step4.png) |

Conclusion: the prototype has the same gap as production; the problem originates in the prototype design, not only in the production implementation.

### Prototype after

| Template details (shows reporting target) | One-click launch (shows reporting target) | Step 4 (saving template includes reporting) |
|---|---|---|
| ![Prototype after template details](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-after-template-detail.png) | ![Prototype after one-click launch](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-after-template-launch.png) | ![Prototype after step 4](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-382-eval-template-badcase-evidence-20261122/proto-after-wizard-step4.png) |

After the fix, template save, details, one-click launch and list status all include the self-evolution reporting target; the token only shows "encrypted at rest, never echoed".
