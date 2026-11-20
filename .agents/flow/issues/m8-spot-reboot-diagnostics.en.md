# Strengthen cold spot runner reboot diagnostics

English | [中文](m8-spot-reboot-diagnostics.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - runner/lib/diagnostics.mjs
  - backend/app/services/provisioning/spot_cloud_init.py
  - tests/sigterm-diagnostics.test.mjs
  - tests/backend/test_spot_cloud_init.py
  - .agents/flow/issues/m8-spot-reboot-diagnostics.md
  - .agents/flow/issues/m8-spot-reboot-diagnostics.en.md
  - .agents/flow/issues/m8-spot-reboot-diagnostics.i18n.yaml
adr: []
github:
  number: 345
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/345
```

## Background

During the 2026-11-19 production contended-spot run with six cases, four runners received SIGTERM while the Agent was executing and the platform settled them as `runner_lost`. The existing `diagnostics/sigterm.json` only collects `journalctl -u github-runner`, auth/syslog and cloud-init tails, which cannot answer who triggered a system-level or userspace reboot. The Aliyun spot termination metadata path is also wrong: it queries `spot/termination-time` instead of `instance/spot/termination-time`, so the 404 cannot prove that no spot interruption notice existed.

## Goals

- Extend diagnostics with the full system journal (including lines before the stop), `journalctl --list-boots`, the previous boot, apt and unattended-upgrades logs, the audit log, reboot-required, timers and kernel logs.
- Fix the Aliyun spot termination metadata path.
- Install and enable auditd in spot cloud-init, record `systemctl`, `reboot` and `shutdown` execve calls plus reboot-required writes, and enable a persistent journal.
- Fix `ExecStopPost=/sbin/shutdown`, which fails to spawn on the real image.

## Acceptance

- `buildSigtermDiagnostics` emits the new fields and requests `instance/spot/termination-time`; tests cover the normal and degraded paths.
- The generated spot bootstrap contains the auditd rules, persistent journal and a working shutdown command; the existing 16 KiB UserData limit still holds.
- `npm test`, `pytest tests/backend` and `npm run verify:flow` pass. After deployment, newly created cold spot instances pick up the change, and the next SIGTERM either names the reboot initiator in `diagnostics/sigterm.json` or explicitly shows no caller.
