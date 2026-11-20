# Disable runtime auto-upgrades on cold spots and pin the runner host image

English | [中文](m8-spot-disable-auto-upgrade.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - backend/app/services/provisioning/spot_cloud_init.py
  - runner/lib/diagnostics.mjs
  - tests/backend/test_spot_cloud_init.py
  - tests/sigterm-diagnostics.test.mjs
  - tests/integration/m4_04_spot_smoke.py
  - tests/runner-image.test.mjs
  - infra/runner-image/**
  - .github/workflows/eval-shard.yml
  - docs/architecture.md
  - docs/architecture.en.md
  - docs/architecture.i18n.yaml
  - scripts/doc-budgets.manifest.json
  - .agents/flow/issues/m8-spot-disable-auto-upgrade.md
  - .agents/flow/issues/m8-spot-disable-auto-upgrade.en.md
  - .agents/flow/issues/m8-spot-disable-auto-upgrade.i18n.yaml
adr: []
github:
  number: 348
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/348
```

## Background

Evaluation 8f43561f ran on the contended spot pool on 2026-11-20; astropy__astropy-12907 received SIGTERM about 13 minutes into Agent execution. The diagnostics deployed by #346 showed that the cold spot instance had apt-daily/unattended-upgrades enabled by default; a random timer fired inside the 60-minute evaluation window, upgraded system packages such as libc and systemd, and needrestart restarted services, so systemd stopped github-runner. There was no spot interruption; the root cause is runtime auto-upgrade.

## Goals

- Make cloud-init write the APT Periodic 0 policy, mask the apt-daily/unattended-upgrades units, and disable needrestart service restarts before any package operation.
- Bake docker, Node, auditd and the other dependencies into a prebuilt host image; instances from that image skip runtime apt and Node downloads, while a stock cloud image keeps one `--no-upgrade` compatibility install.
- Provide a fixed-cadence image rebuild, version record, pool switch and rollback flow so security patches land at build time.
- Refuse jobs when `/var/run/reboot-required` exists at startup; hold `systemd-inhibit --what=shutdown:sleep` for the job lifetime to block shutdown and sleep.
- Let SIGTERM diagnostics and runner-smoke directly confirm each instance's auto-update state and image source.

## Acceptance

- A new cold spot instance reports all six auto-update units masked, APT Periodic values 0 and no needrestart restart; UserData stays within 16 KiB.
- The runner does not start when a reboot is required; the live runner unit carries the shutdown/sleep inhibitor.
- The image build script supports dry-run and writes a version record with the image_id; the docs cover weekly rebuild, CVE response, pool switch and rollback.
- `npm test`, `pytest tests/backend`, the docs and flow gates pass; runner-smoke verifies the hardening on a real cold spot instance.
