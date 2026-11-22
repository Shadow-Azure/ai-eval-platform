# [m8-54] Sign image tarball URLs per case after capacity admission

English | [中文](m8-54-image-tarball-lazy-presign.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-54-image-tarball-lazy-presign.*
  - .agents/notes/adr-0003-image-tarball-lazy-presign.*
  - docs/design/eval-platform-architecture.*
  - runner/scripts/real-task.mjs
  - runner/lib/task-payload.mjs
  - runner/lib/image-tarball.mjs
  - backend/app/services/dataset_image_tarballs.py
  - backend/app/api/runners.py
  - tests/runner/**
  - tests/backend/**
adr:
  - ../../notes/adr-0003-image-tarball-lazy-presign.md
github:
  number: 371
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/371
```

## Background

Production evaluation `148b0b6d-86bc-4739-8b48-28d9caadccee` (MiniMax-M3.1-Flash × fixed-linux × OpenSpec-cn, six SWE-bench cases, Agent timeout 7200s, case timeout 8400s, concurrency 1) ended `partial`: two cases executed but stayed unresolved, and four failed with `image_unavailable` before the Agent started.

The GitHub Actions job logs of all four failed cases show `image_tarball_fallback` with `curl: (22) The requested URL returned error: 403` at the download stage; the runner then fell back to the public mirror sources `docker.1ms.run`, `proxy.vvvv.ee`, `docker.jiaxin.site`, and `free.hubfast.cn`, all of which errored or hit their usage limit. The OSS objects exist (HEAD measured about 1.12GB each), so this is not a missing cache.

UTC timeline: the six execute jobs started around 14:41 and fetched `/tasks`; run concurrency was 1, so the first two cases ran until 16:40; the remaining four got capacity at 16:41–16:56, by which time the presigned URLs issued at 14:41 with a 3600s TTL had been expired for about an hour. Case 2 downloaded with the same URLs at 15:30 (41s), just within the TTL.

## Root cause

`runner/scripts/real-task.mjs` fetches `/tasks` once at job start (L1151), and the platform signs an OSS presigned URL for each case right then; the capacity wait happens later (the `started` callback 429 retry at L1425, bounded by `case_timeout + 300s`, 8700s here). The image download happens after capacity is granted, so the URL age equals the queue time plus startup overhead and can far exceed the TTL (`dataset_image_tarball_ttl_seconds` defaults to 3600s in `backend/app/services/dataset_image_tarballs.py`).

Two amplifying factors:

- All case jobs in a shard can land on idle runners at once (fixed and spot share the `self-hosted,linux,eval` labels) and queue on the platform concurrency limit rather than in the GitHub queue; the runners stay occupied and the URLs age with them.
- Public mirror sources are unreliable, so using them as a fallback turns an expired credential into "image unavailable".

## Fix plan

Move each case's `/tasks` (or a dedicated image-tarball endpoint) fetch to after the `started` callback succeeds and before the image pull:

1. `runner/scripts/real-task.mjs`: keep the early manifest fetch; make `taskByCase` lazy — after `postStartedWithCapacityWait` returns 2xx, fetch `/tasks` again with the same runner token (or call the per-case URL endpoint), then download the image.
2. TTL: the presigned URL only needs to cover the download (15–30 minutes), not the queue; `backend/app/services/dataset_image_tarballs.py` computes it from the download budget plus margin instead of scheduling parameters.
3. 403 fallback: on the first 403, refresh `/tasks` once and retry once; only then fall back to `image_sources`, distinguishing auth failure from a missing object.
4. Tests: runner unit tests cover "no `/tasks` fetch or URL signing before capacity", "a fresh URL after a 429 wait", and "the 403 refresh retries once"; backend unit tests cover the TTL calculation.

Non-goals (separate issues): moving capacity queueing into the scheduler (`max_parallel` / platform queue), fixed/spot label isolation, and the long-term internal registry plus short-lived token.

## Acceptance

- Under the reproduction conditions (six cases, concurrency 1, cases of 50+ minutes), the four queued cases no longer hit 403 when pulling images and `image_unavailable` is 0.
- Unit assertions: the `/tasks` request happens after `started` 2xx; the URL TTL is computed only from the download budget; the 403 refresh retries once.
- `npm test`, `npm run verify:flow`, and `npm run verify:docs` pass.
- Docs: the design doc and ADR-0003 describe the contract "sign per case after capacity admission, TTL covers the download only".

## References

- ADR: `.agents/notes/adr-0003-image-tarball-lazy-presign.md`
- Design doc: `docs/design/eval-platform-architecture.md` sections 4 and 7
