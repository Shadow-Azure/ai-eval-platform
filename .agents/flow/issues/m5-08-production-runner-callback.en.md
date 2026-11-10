# Enable the production runner callback entry for the spot pool and verify it end to end

English | [中文](m5-08-production-runner-callback.md)

```yaml flow
kind: issue
milestone: m5
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m5-08-production-runner-callback.*
  - .agents/flow/issues/m5-05-eval-results-workbench.*
  - .agents/flow/milestones/m5-product-workflows.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - deploy/**
  - .github/**
  - runner-contract/**
  - scripts/doc-budgets.manifest.json
  - plans/**
adr: []
github:
  number: 119
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/119
```

## Background

M4-03/M4-04 already provide resource connections, spot provisioning and JIT ephemeral runners, and M5-03 shipped the connection/pool pages; the production runner plane is still disconnected: the API only listens on loopback, the security group does not allow the VPC callback port, worker and api lack the callback base URL, and spot instances cannot fetch the manifest or post results. The provisioning test releases the instance before registration, and eval-shard executes on hard-coded default labels, so a custom-label pool registers runners that never receive a job.

## Goals

- Expose the Runner callback entry inside the VPC with the smallest surface, keep the fixed runner's loopback path, and add no public ingress.
- Persist the callback base URL, the runner tarball cache and related deployment settings in the repository and on the host `.env`, so redeploying or moving hosts loses nothing.
- Freeze the pool labels in the shard dispatch and manifest so custom-label pools execute on their own runners.
- Verify creation, registration, execution, callback and release on a real spot pool with 5 cases at concurrency 5.

## Tasks

| Task | Content | Owner |
|---|---|---|
| Deployment | Add a VPC binding beside loopback and `RUNNER_CALLBACK_BASE_URL` in compose; write the host `.env` from deploy.yml | Code |
| Label dispatch | Carry `runs_on` in dispatch/manifest; eval-shard prepare outputs it and execute uses it | Code |
| Provisioning test | Keep creation-verification semantics and state in UI/docs that it does not register a runner | Docs |
| Security group | Allow TCP 8000 from the VPC CIDR and confirm private-only access | Human |
| Secret injection | Set `API_BIND_ADDRESS` (the callback base URL is derived) and the optional runner tarball URL | Human |
| End-to-end check | Create the Aliyun connection and spot pool, run 5 cases at concurrency 5 and keep evidence | Human |

## Acceptance

- Production API listens on the VPC address while keeping loopback, and 8000 is open only to the VPC CIDR.
- Spot-pool runners can fetch the manifest and post results; shard labels match the labels the pool registers.
- With 5 cases at concurrency 5, peak running=5, 5/5 callbacks arrive and instances are released automatically, with evidence recorded in the PR description.
- Deployment settings are committed to the repository, so a redeploy or host move restores the callback entry without code changes.

## Production enablement checklist

- [ ] Allow inbound TCP 8000 from the VPC CIDR in the security group.
- [ ] Set `API_BIND_ADDRESS` in GitHub Secrets; `RUNNER_CALLBACK_BASE_URL` is derived from it by default and may be overridden.
- [ ] Merge this PR and confirm the automatic deployment succeeds.
- [ ] Create the connection and spot pool with the existing Aliyun AK, run 5 cases at concurrency 5 and accept the result.
