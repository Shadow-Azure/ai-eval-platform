# Fix model connectivity probing for TokenHub Anthropic endpoints

English | [中文](m8-model-connectivity-tokenhub.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/app/api/model_configs.py
  - tests/backend/test_model_gateway.py
  - web/src/pages/ModelsPage.tsx
  - .agents/flow/issues/m8-model-connectivity-tokenhub.*
  - reports/2026-11-21/m8-model-connectivity-tokenhub/*
adr: []
github:
  number: 369
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/369
```

## Background

The production DeepSeek configuration uses the Tencent Cloud TokenHub Anthropic-compatible endpoint (`https://tokenhub.tencentmaas.com/plan/anthropic`). The console connectivity test requests `GET {base_url}/v1/models`; TokenHub answers the model listing with 404, so the test reports an invalid model request and the operator keeps replacing a valid key. The gateway's real call, `POST {base_url}/v1/messages`, returns 200 with the same key, so the failure comes from the probe path rather than the credential.

## Goals

- Probe the endpoint the gateway actually calls, selected by `api_style`, with one minimal model request.
- Keep the Base URL helper text in the console consistent with the new probe.
- Add a regression test for the TokenHub shape (no model listing, working messages endpoint).

## Acceptance

- An Anthropic configuration passes the connectivity test when `GET /v1/models` returns 404, and only `/v1/messages` is called.
- An OpenAI-compatible configuration still probes `/v1/chat/completions`, and 401/5xx keep mapping to the `secret` / `base_url` fields.
- The targeted backend tests and the frontend typecheck pass.
