# Account-password login and session access control

English | [中文](m3-05-account-auth.md)

```yaml flow
kind: issue
milestone: m3
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m3-05-account-auth.*
  - .agents/flow/milestones/m3-first-real-evaluation.*
  - backend/**
  - web/**
  - deploy/**
  - .github/workflows/**
  - tests/**
  - docs/**
  - reports/**
  - docker-compose.yml
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 53
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/53
```

## Background

Design section 5.1 and requirement 1 call for account-password login, salted password hashing, signed sessions, and default-tenant membership, with an explicit failure state for wrong passwords, a login redirect for unauthenticated deep links, and no reuse after logout. M2 shipped only a demo login page, and the control-plane API still allows anonymous reads of real data. M3 starts carrying real datasets and evaluation results, so the public control plane must first be narrowed to authenticated sessions; otherwise the deployed link keeps exposing real data. The user confirmed this gap belongs in M3 and asked for implementation.

## Goals

- Add the identity data model and migration: `user`, `membership`, `auth_session`, and `audit_log`, with an idempotently created administrator membership under the default tenant.
- Provide `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, and `GET /api/v1/auth/me`: scrypt password hashing, an HMAC-signed random session token, an HttpOnly/SameSite=Lax cookie, and only the token hash, expiry, and revocation state stored in the database.
- Narrow control-plane access: `/api/v1/*` business routes require an authenticated session while the Runner plane keeps its own short-lived tokens; administrator and read-only member roles, with writes limited to administrators.
- Wire the frontend to real login: an explicit failure state for wrong passwords, a login redirect that returns to the original deep link, session-expiry handling, current user and logout in the top bar, and removal of demo credentials.
- Complete deployment and documentation: session-secret and initial-administrator configuration, an administrator password-reset command, Compose and workflow wiring, and updated architecture and development notes.

## Acceptance

- A correct login returns the user, tenant, and role and sets an HttpOnly session cookie; a wrong password, unknown account, or disabled account all return the same explicit 401 failure without revealing whether the account exists.
- The database stores only the scrypt password hash and the SHA-256 session token hash; an expired or revoked session's old cookie is unusable, and logout revokes the current session immediately.
- An unauthenticated `GET /api/v1/overview` returns the 401 error envelope; opening a protected deep link directly redirects to the login page and returns to the original target after login.
- A read-only member receives 403 from write endpoints such as evaluation creation/cancellation and dataset import; an administrator can execute them, and Runner-plane heartbeats and callbacks are unaffected by control-plane sessions.
- Frontend build and typecheck pass; pytest covers password hashing, login success/failure, session expiry and revocation, role gating, and seed idempotency; the PR includes before/after login screenshots at the same viewport.
- Deployment can create the initial administrator without committing a password, and API responses and logs contain no plaintext password or session token.
