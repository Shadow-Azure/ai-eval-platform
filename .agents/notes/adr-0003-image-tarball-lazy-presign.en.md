# ADR 0003 · Signing point and TTL boundary for image tarball presigned URLs

English | [中文](adr-0003-image-tarball-lazy-presign.md)

- Status: Accepted
- Date: 2026-11-22
- Applies to issue: m8-54 (sign image tarball URLs per case after capacity admission)
- Design basis: `docs/design/eval-platform-architecture.md` sections 4 and 7; failure evidence from production evaluation `148b0b6d-86bc-4739-8b48-28d9caadccee`

## Background

The runner fetches `/tasks` when an execute job starts, and the platform signs a short-lived presigned OSS URL for each case's image tarball right then; only afterwards does the runner wait for the platform concurrency limit on the `started` callback. In the production evaluation, four cases waited more than two hours in the concurrency queue, and by the time they downloaded, their 3600s URLs had expired, the downloads returned 403, and the fallback public mirror sources were all unavailable, ending in `image_unavailable`. The OSS objects themselves were intact; the failure was unrelated to whether a cache existed.

Simply extending the TTL to "shard case count × case timeout" would still expire again under retries, pauses and resumes, concurrent queueing, and runner preemption; it would also enlarge the exposure of unrevocable bearer URLs for 1.1GB-class images. The root cause is the coupling between the signing moment and the scheduling wait.

## Decision

1. Image tarball presigned URLs must be signed per case after capacity admission. The runner fetches `/tasks` (or calls a per-case image-tarball endpoint) only after the `started` callback returns 2xx, and downloads the image immediately.
2. The presigned TTL covers only the download window, 15–30 minutes by default, computed from the download budget plus margin; it does not cover the queue.
3. On the first 403 download, refresh `/tasks` once and retry once; only then fall back to `image_sources`, classifying auth failures separately from missing objects.
4. The capacity-wait bound stays `case_timeout + 300s`; this decision does not move the queue or change the concurrency model.
5. Do not adopt "extend the TTL to N × case_timeout or 24 hours" as the solution; it only pushes the race further away and enlarges the exposure of unrevocable credentials.

## Alternatives

- Long TTL: rejected. Unrevocable, coupled to scheduling parameters, and still expirable.
- Platform-proxied download or an internal registry with short-lived tokens: a more thorough direction, to be tracked separately.
- Scheduler-level capacity admission (`max_parallel` / platform queue): complementary to this change, to be tracked separately.

## Impact

- The runner's `/tasks` call timing changes; the `/tasks` response shape and the platform API contract stay unchanged.
- Design doc sections 4 and 7 describe the contract "sign per case after capacity admission, TTL covers the download only"; `docs/architecture.md` follows the code PR once the behavior lands.
- Tests must cover no `/tasks` fetch before capacity, a fresh URL after a 429 wait, a single 403 refresh, and the TTL calculation.
- Rollback: keep a TTL floor as a safety net; falling back to fetching `/tasks` at job start requires a TTL no smaller than the capacity-wait bound and is not recommended.
