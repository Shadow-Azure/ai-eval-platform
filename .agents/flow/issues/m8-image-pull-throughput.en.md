# Image pull throughput: serial preparation, unstable registries, and missing prewarm

English | [中文](m8-image-pull-throughput.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-image-pull-throughput.*
  - backend/app/services/dataset_image_prep.py
  - backend/app/worker/image_prep.py
  - backend/app/services/dataset_image_artifacts.py
  - backend/app/services/dataset_images.py
  - backend/app/config.py
  - backend/app/api/**
  - deploy/docker-compose.prod.yml
  - deploy/proxy/**
  - tests/backend/**
  - tests/deploy/**
  - docs/**
  - research/**
  - plans/**
adr: []
github:
  number: 439
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/439
```

## Background

The dataset image preparation service pre-pulls case images into OSS tarballs for cold-start runners. It is a single-replica, one-image-at-a-time pipeline: pull -> tag -> `docker save`/gzip -> upload -> cleanup, then claim the next image. During acceptance one dataset was still missing four images and another dataset's 30 images had not been enqueued; a single registry source measured ~2MB/s, and images are ~1-2GB, so pulling alone takes hours and launch is blocked by `409 image_not_ready`. This issue records the code and deployment diagnosis, root cause, and P0/P1 options.

## Diagnosis

- Code path: `prepare_one` claims one row at a time; `candidate_ref_pairs` tries configured sources in order, with a 1800s pull timeout; on success it runs `docker save | gzip -1`, uploads to internal OSS, and `_cleanup` deletes the image and temp tar; the worker is a single replica.
- Queue and prewarm: `dataset_image_artifact` rows are enqueued only by prepare/create; an unlaunched dataset has no rows, so it is never warmed. Claims order by `next_attempt_at, id`; a batch shares the same timestamp, so it is effectively UUID order; create requires every image ready, so one slow image blocks the whole run.
- Registries and failures: `DATASET_IMAGE_REGISTRIES` is empty in the deployment `.env`; the effective list is a DB override with 8 sources (1ms.run, VVVV, Jiaxin, HubFast, 1Panel, Docker Hub, Aliyun ACR, Tencent Mirror). image-prep logs show some images succeed via VVVV/1ms.run (tarballs ~1.2GB), but also 1ms.run `unexpected EOF`, `Cannot connect to the Docker daemon`, `docker tag` `No such image` after a pull, and some images failing on all 8 sources. DinD has no `/etc/docker/daemon.json` registry mirror; the host Docker has an Aliyun mirror.
- Cleanup and reuse: after upload, all candidate images and the temp tar are deleted; the next image re-downloads shared base layers; each tarball contains all layers, so shared layers are duplicated in OSS and on runner downloads.
- Deployment proxy test: the existing `proxy` is Squid with an ACL for `.github.com` and `.githubusercontent.com` only, and `cache deny all`; from the deployment ECS, `curl -x` to `registry-1.docker.io:443` returns `TCP_DENIED/407` while `api.github.com` returns 200; direct Docker Hub from the host times out within 20s. The proxy and image-prep are on the same ECS with the same egress and no cache, so it cannot add Docker pull bandwidth.

## Conclusion and root cause

Conclusion: the main cause is not a missing proxy but a serial single worker, slow/unstable upstream registries, and missing prewarm; the proxy is a GitHub-only channel and cannot fix Docker pulls.

- Root cause 1: single replica and one image at a time; pull, save, upload and cleanup do not overlap, so throughput is bounded by one image.
- Root cause 2: the 8 sources have no health check or throughput ordering; the first source 1ms.run returns `unexpected EOF`, dead/slow sources consume time; Docker Hub is unreachable directly and DinD has no daemon-level mirror.
- Root cause 3: the queue is populated only by prepare/create; unlaunched datasets are never warmed, and all images must be ready before launch, so a slow image blocks acceptance.
- Root cause 4: all local images/layers are deleted after upload, so shared base layers are re-downloaded; per-image tarballs duplicate shared layers in OSS and on runners.
- Root cause 5: the GitHub-only Squid shares the host and egress with image-prep and has no cache; adding registry domains would not add international bandwidth, only a hop and a wider security boundary.

## P0 actions

1. Prewarm: use the existing prepare/ensure flow to enqueue all missing images for the pending datasets (especially the 30 images that were never enqueued), then launch; add a "prewarm by dataset version" entry instead of relying on prepare.
2. Source selection: probe the 8 sources with a small image/manifest, record TTFB, success rate and throughput, and sort by measured results; skip dead sources with a short timeout; put Docker Hub last or configure a daemon-level registry mirror for DinD; remove long-dead sources.
3. Concurrency and metrics: raise DinD `max-concurrent-downloads` from 3 to 8; record per-image pull seconds/bytes, save seconds, upload seconds, tarball size and source, to identify whether pull or save/upload dominates.
4. Proxy conclusion: do not use the GitHub-only Squid for Docker pulls; it denies registry traffic and is same-host, same-egress, no-cache. If a dedicated proxy is evaluated, deploy it on a host with a better route, allowlist only registry domains, and measure throughput first.

## P1 actions

1. Image-level concurrency: 2-4 concurrent preparations (thread pool or multiple workers), each with its own DB session/workdir; owner-scoped cleanup; startup sweep must not delete other workers' temp tars or reset their preparing rows; admit by free disk.
2. Direct tarball: use `skopeo`/`crane` to write docker-archive directly from the registry, with `--image-parallel-copies` for layers and `--retry-times` for retries, avoiding the pull+save double write; verify `docker load` compatibility.
3. Layer reuse and dedup: keep a bounded local image/layer cache or a local registry; dedup by manifest digest so the same digest under different tags is not pulled/transferred twice; measure shared-base-layer reuse.
4. Queue priority: order by dataset/run/case need instead of UUID; expose queue position and ETA; prefetch on dataset import/version creation.
5. Registry/proxy: evaluate Harbor proxy cache or a private registry as the VPC distribution layer; if a proxy is required, deploy a dedicated registry proxy and measure throughput/cost; do not reuse the GitHub-only Squid.

## Expected speedup (measured revision, further validation needed)

Deployment ECS temp-DinD measurements while the production worker was pulling: a 48.5MB layer took 28.0s on VVVV (1.73MB/s), 55.9s on HubFast (0.87MB/s), and >120s on 1Panel/1ms (<0.40MB/s); a 1.2GB dataset image on VVVV downloaded about 801MB in 10.7min (1.25MB/s) and did not finish; a 100MB domestic mirror test reached 7.57MB/s, so the host egress is not the 2MB/s cap and the bottleneck is the registry upstream; two concurrent pulls (1Panel + VVVV) showed about 3.8MB/s host rx, so concurrency can aggregate bandwidth.

- Source selection/health: about 1.2-1.5x; no measured 8-12MB/s source, so the main benefit is avoiding dead sources and failed retries.
- 2-4 concurrent pulls: about 2-3x; at 3.8-6MB/s aggregate, 36GB drops from ~5h to 1.7-2.6h, plus save/upload, about 2-3h total.
- Shared-layer reuse (not measured): if the 30 images share base layers, another 10-30% of transferred bytes can be saved.
- Prewarm: total preparation time is unchanged, but it moves off the acceptance critical path; if completed early, acceptance-side wait can approach 0.
- A dedicated proxy/ACR/GA could reach 4x+ only if it raises a single source from 2MB/s to 10MB/s+; the current same-host Squid does not.

## Acceptance

- Prewarm: all images for the pending datasets are enqueued before launch and visible in readiness; no 409 for unqueued images.
- Source selection: health probes skip dead sources and pick the fastest measured source; each source's failures and latency are recorded.
- Throughput: three representative images show a measurable improvement in pull/preparation time (target set after the benchmark); concurrent preparation shows no duplicate pulls or cleanup overlap.
- Proxy: documentation states the GitHub-only Squid cannot be used for Docker; if a dedicated proxy/registry lands, record throughput, cost and allowlist scope.
- Regression: backend/deploy targeted tests, `npm test`, `npm run verify:docs` and `npm run verify:flow` pass; runtime changes are limited to the image preparation path.

## Discussion

The proxy test says it cannot speed things up: the current Squid allows GitHub only and rejects Docker registry CONNECT; it shares the host and egress with image-prep and has `cache deny all`, so opening registry domains would not add bandwidth. A temporary Squid copy with the same image and an allow-all config was also tested: `registry-1.docker.io:443` timed out at 30s, `auth.docker.io` returned CONNECT 503, and `api.github.com` returned 200, confirming the bottleneck is the ECS egress, not the ACL. To speed up, prefer a faster registry mirror/ACR/GA, or put the proxy/registry on a host with a better international route; whether to build a dedicated Docker proxy or private registry needs a decision on cost, operations and security boundary.
