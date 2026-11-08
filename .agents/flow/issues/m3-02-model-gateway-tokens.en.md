# Central model gateway with short-lived model tokens

English | [中文](m3-02-model-gateway-tokens.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/issues/m3-02-model-gateway-tokens.*
  - backend/**
  - runner/**
  - deploy/**
  - tests/**
adr: []
github:
  number: 46
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/46
```

## Background

Decision D3 is confirmed: model keys are decrypted only in the control plane, Runners call the platform's central model gateway with short-lived tokens, and real keys never enter a Runner. The first real case of M3 needs unified forwarding for OpenAI-compatible endpoints plus minimal credential storage and usage recording. M2-05 already established the platform HMAC short-token pattern that can be reused directly.

## Goals

- Implement model configuration and credential storage: API responses return masked values, storage uses envelope encryption (a simplified host-key envelope is acceptable for now with the evolution point annotated in code).
- Implement the central model gateway endpoint: forward OpenAI-compatible requests with unified timeout, retry, and error mapping.
- Implement short-lived model credentials for Runners: issue, verify, and expire, reusing the M2-05 `short_token` wire format and cross-language test pattern.
- Record minimal model usage and cost metrics, queryable per evaluation and per shard.

## Acceptance

- No API response ever echoes credential plaintext; only masked values are shown.
- A Runner with a short-lived token completes a real chat completion through the gateway (CI may use a stub endpoint; deployment verification uses the real endpoint).
- Invalid, expired, or shard-mismatched tokens are rejected by the gateway with explicit error classes.
- Usage metrics persist and are queryable per evaluation/shard; pytest covers issuance, expiry, forwarding failure, and masking paths.
