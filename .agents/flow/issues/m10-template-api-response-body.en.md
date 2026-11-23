# [m10] Show response bodies for template list and launch APIs in the API modal

English | [中文](m10-template-api-response-body.md)

```yaml flow
kind: issue
milestone: m10
priority: P2
status: ready
scope:
  - prototype/**
  - web/src/components/TemplateApiModal.tsx
  - web/src/styles/templates.css
  - plans/**
  - reports/**
  - .agents/flow/issues/m10-template-api-response-body.md
  - .agents/flow/issues/m10-template-api-response-body.en.md
  - .agents/flow/issues/m10-template-api-response-body.i18n.yaml
adr: []
github:
  number: 406
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/406
```

## Background

The production "template API" modal (template list and template detail) lists four APIs: template list GET, trigger launch POST, status GET, and results GET. Status and results already show a response body, but the two APIs boxed in the user screenshot, "list templates" and "trigger launch", only show a description and a curl request example. The prototype's `templateApiBody` in `prototype/app.js` matches the production `TemplateApiModal` and is missing the same two responses.

## Goals

- Add a list response example to `GET /api/v1/eval-templates` in the prototype (`status`, `items`, `total`, `limit`, `offset`).
- Add a `202 Accepted` response example to `POST /api/v1/eval-templates/{template_id}/launch` in the prototype (`run_id`, `template_id`, `status`, `status_url`, `result_url`, `created`); leave spacing between the curl request and the response.
- Add both responses to production `TemplateApiModal` with the same design, without changing APIs, request fields, or the modal's existing layout.
- Keep before/after screenshots on the same viewport, template, and theme; attach the comparison to the issue and the before/after screenshots to the implementation PR.

## Acceptance

- At 1500×837 both response bodies are visible in the modal's first screen; the modal still fits the viewport, the body scrolls, the footer stays fixed, and there is no horizontal overflow.
- List response fields match `template_view`, the launch response includes the backend's actual `created` field, and examples use the current template ID.
- Production and prototype share the same structure: code block styling, request/response order, and the existing status/results examples stay unchanged.
- `npm test`, `npm run verify:flow`, `npm run lint:docs`, and `npm run verify:docs` pass.
- Prototype before/after screenshots are attached to the issue and the corresponding PR.
