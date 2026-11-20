# Cold spot automatic security updates kill in-flight runner jobs

English | [中文](m8-spot-auto-update-sigterm.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - backend/app/services/provisioning/spot_cloud_init.py
  - tests/backend/test_spot_cloud_init.py
  - runner/lib/diagnostics.mjs
  - tests/sigterm-diagnostics.test.mjs
  - docs/architecture.md
  - docs/architecture.en.md
  - .agents/flow/issues/m8-spot-auto-update-sigterm.md
  - .agents/flow/issues/m8-spot-auto-update-sigterm.en.md
  - .agents/flow/issues/m8-spot-auto-update-sigterm.i18n.yaml
adr: []
github:
  number: 348
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/348
```

## Background

Evaluation 8f43561f ran in the contended spot pool on 2026-11-20; astropy__astropy-12907 received SIGTERM after about 13 minutes of Agent execution and settled as runner_lost. #346 added auditd, persistent journal and apt/unattended-upgrades diagnostics, which captured the full evidence this time: cold spot instances enable apt-daily/unattended-upgrades by default, a random timer landed inside the 60-minute evaluation window, automatic system package upgrades triggered a service restart, and systemd stopped the runner.

## Analysis

- Run context: 12907 attempt 1, job 44662253385, instance i-bp10mlmax6tmr2da04eb, created at 02:36:36 after the #346 deploy, SIGTERM at 02:52:34.
- Automatic update: apt-daily-upgrade.timer fired at 10:51:37; from 10:51:49 to 10:52:31 unattended-upgrade upgraded libc6, libc-bin, locales, systemd, perl, python3-twisted and others.
- Reboot-required: /var/run/reboot-required contained “*** System restart required ***”.
- Restart evidence: at 10:52:34 systemd entered final-sigterm and networkd, sshd, containerd and auditd restarted together; journalctl --list-boots still showed one boot (10:36:54–10:52:35), so this was a userspace/service restart rather than a full reboot.
- Platform: provider_state=Running, no OperationLocks, and spot termination-time returned 404 (no spot interruption notice); this was not a cloud-side reclaim. The last 60 audit lines only contained our own systemctl diagnostics calls, with no explicit reboot command.
- Root cause: the unattended-upgrades package upgrade triggered a service restart (needrestart/systemd path), systemd stopped github-runner, and the job received SIGTERM.

## Solution

- Immediate fix: disable runtime automatic updates in cloud-init by writing APT::Periodic::Update-Package-Lists "0" and related settings, masking apt-daily.timer, apt-daily-upgrade.timer, unattended-upgrades.service and ua-timer.timer, and disabling automatic service restarts in /etc/needrestart/conf.d.
- Bake dependencies: put docker, node and auditd into the runner image; cloud-init should no longer run apt-get update/install, so boot does not incidentally upgrade packages.
- Long-term: follow immutable infrastructure with periodic image rebuilds (for example weekly from the latest Ubuntu base image, test, then update the pool image_id/digest); security patches land at build time and instances always boot from a patched image.
- Safety net: wrap the execute job with systemd-inhibit --what=shutdown:sleep --mode=block; if /var/run/reboot-required exists at boot, the runner should refuse jobs instead of rebooting mid-job.
- Security trade-off: after disabling runtime auto updates, an image update SLA, CVE monitoring and an emergency rebuild path are mandatory; do not disable updates without a patched image pipeline.

## Acceptance

- On newly created cold spot instances apt-daily.timer and apt-daily-upgrade.timer are masked, APT Periodic settings are zero, and needrestart does not restart services automatically.
- A 60-minute evaluation window no longer shows unattended-upgrade-driven service restarts or SIGTERM; the apt/unattended fields in diagnostics/sigterm.json are empty or only contain boot-time installs.
- Generated cloud-init UserData stays within the 16 KiB limit and the related unit tests cover it; the change takes effect on new instances after deployment.
- The image rebuild process has a fixed cadence and version record, and the pool can roll back after switching.

## Risks

- Skipping runtime updates can leave the image stale; fixed-cadence rebuilds and CVE response must compensate for it.
- systemd-inhibit is not a hard boundary; forced kills or hardware events can still bypass it, so it cannot replace disabling timers and refreshing the image.
- Existing running instances do not change automatically; the change only applies to new cold spot instances.

## References

- Ubuntu automatic updates: https://ubuntu.com/server/docs/how-to/software/automatic-updates
- AWS OPS03-BP05 immutable patching: https://docs.aws.amazon.com/wellarchitected/latest/framework/ops03-bp05.html
- Ubuntu needrestart services restart: https://lists.ubuntu.com/archives/ubuntu-devel-discuss/2024-June/019711.html
- systemd-inhibit: https://www.freedesktop.org/software/systemd/man/256/systemd-inhibit.html
