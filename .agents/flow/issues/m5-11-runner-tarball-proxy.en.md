# Serve the runner tarball through a permanent platform proxy

English | [中文](m5-11-runner-tarball-proxy.md)

```yaml flow
kind: issue
milestone: m5
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m5-11-runner-tarball-proxy.*
  - backend/**
  - tests/**
  - docs/**
  - deploy/**
  - .github/**
  - scripts/**
adr: []
github:
  number: 130
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/130
```

## Background

PR #129 caches the actions-runner archive in OSS for Aliyun spot runners and injects a presigned GET URL into `RUNNER_SPOT_RUNNER_TARBALL_URL` at deploy time. A SigV4 presigned URL lasts at most seven days: when no deploy happens for longer, newly created spot instances receive 403 while downloading, cloud-init installation fails, and the runner never registers; the object stays in OSS and only the address expires.

## Goals

- Expose a fixed `/runner/v1/runner-tarball/<version>.tar.gz` endpoint where the api reads the object with OSS credentials and streams it back, so the download address no longer expires with a presigned URL.
- Reuse the heartbeat token for download authorization and keep the OSS bucket private, without public-read or a long-lived public address.
- Make spot cloud-init download through that proxy by default and attach the bearer token; an explicit `RUNNER_SPOT_RUNNER_TARBALL_URL` is treated as an external mirror and receives no token.
- Keep the deploy pipeline responsible only for caching the archive in OSS and stop writing the seven-day presigned URL into the host `.env`; fall back to the GitHub URL when object storage is unconfigured.

## Acceptance

- The fixed proxy address for a version stays downloadable across deploys, so a gap longer than seven days does not affect new instances.
- A wrong or missing token returns 401, an unknown version returns 404, and unconfigured object storage or a failed read returns 503/502.
- Cloud-init carries the token in proxy mode and omits it for an external mirror; backend tests cover URL derivation, authorization, and streaming responses, and the deployment contract test covers no injected presigned URL.
- Documentation describes the proxy endpoint, the override, and the private-OSS premise.
