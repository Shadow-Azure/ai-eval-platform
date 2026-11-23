# [m10] Add a paginated launch-history modal to evaluation templates

English | [中文](m10-template-run-history.md)

```yaml flow
kind: issue
milestone: m10
priority: P2
status: ready
scope:
  - prototype/**
  - .gitignore
  - backend/app/api/evaluations.py
  - backend/app/services/evaluations.py
  - tests/backend/test_eval_templates.py
  - web/src/api/evaluations.ts
  - web/src/components/TemplateHistoryModal.tsx
  - web/src/pages/EvalTemplatesPage.tsx
  - web/src/pages/EvalTemplateDetailPage.tsx
  - web/src/styles/templates.css
  - docs/design/eval-platform-architecture.md
  - docs/design/eval-platform-architecture.en.md
  - docs/design/eval-platform-architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - .agents/flow/issues/m10-template-run-history.md
  - .agents/flow/issues/m10-template-run-history.en.md
  - .agents/flow/issues/m10-template-run-history.i18n.yaml
adr: []
github:
  number: 394
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/394
```

## Background

The production evaluation-template list only offers details, API, disable, and launch on each row, while the template detail page embeds only the five most recent runs. Once a template has been launched many times, users cannot inspect earlier evaluations from the template context or return from the template to a specific historical run. The evaluation-list API already supports status, dataset, and pagination, but has no read contract filtered by template.

## Goals

- Add a history action to each template row and launch-history actions to the detail header and recent-runs card, all opening the same modal.
- Read the template's historical evaluations through server pagination, defaulting to 10 rows per page with 10/20/50 options and filtering by status with counts, including zero counts.
- Open the corresponding evaluation detail from any row or the view action; provide clear empty states for no launches and no filter matches with a reset action.
- Reuse `GET /api/v1/evaluations` with an optional `template_id` filter instead of adding an execution API; keep `recent_runs` on the detail page as a five-item preview.

## Acceptance

- The prototype row and detail entries are visible; the modal defaults to 10 rows per page, and pagination, status filtering, and counts are correct, with filtering resetting to page 1.
- A history row opens the corresponding evaluation detail instead of falling back to a default run; zero-launch and no-match states both have usable empty states.
- Before/after screenshots use the same viewport and theme and are attached to issue #394 and the prototype PR.
- Design section 5.14 records the `template_id` filter, pagination semantics, and read-only boundary.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, `npm run verify:docs`, and PR CI pass.

## References

- Issue: https://github.com/Shadow-Azure/ai-eval-platform/issues/394
- Design: [docs/design/eval-platform-architecture.en.md](../../../docs/design/eval-platform-architecture.en.md)
