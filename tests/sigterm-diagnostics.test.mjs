import assert from "node:assert/strict";
import test from "node:test";

import { buildSigtermDiagnostics } from "../runner/lib/diagnostics.mjs";

test("buildSigtermDiagnostics captures systemd, journal, reboot evidence, metadata and GitHub context", () => {
  const calls = [];
  const fakeExec = (command, args) => {
    calls.push([command, ...args]);
    if (command === "systemctl") {
      if (args.includes("list-timers")) {
        return { status: 0, stdout: "apt-daily.timer timer-a\napt-daily-upgrade.timer timer-b\n", stderr: "" };
      }
      return {
        status: 0,
        stdout:
          "ActiveState=deactivating\nSubState=stop-sigterm\nResult=success\nExecMainStatus=0\nSystemState=running\n",
        stderr: "",
      };
    }
    if (command === "journalctl") {
      if (args.includes("--list-boots")) {
        return { status: 0, stdout: "-1 boot-a\n 0 boot-b\n", stderr: "" };
      }
      if (args.includes("-k")) {
        return { status: 0, stdout: "kernel-a\n", stderr: "" };
      }
      if (args.includes("-b") && args.includes("-1")) {
        return {
          status: 0,
          stdout: "systemd[1]: Reboot requested by unattended-upgrades\njournal line\n",
          stderr: "",
        };
      }
      if (args.includes("-b") && args.includes("600")) {
        return {
          status: 0,
          stdout:
            "systemd[1]: Stopping github-runner.service - GitHub Actions ephemeral runner...\n" +
            "systemd[1]: Reboot requested by unattended-upgrades\n",
          stderr: "",
        };
      }
      return { status: 0, stdout: "line-a\nline-b\n", stderr: "" };
    }
    if (command === "dmesg") return { status: 0, stdout: "dmesg-a\n", stderr: "" };
    if (command === "last") return { status: 0, stdout: "login-a\n", stderr: "" };
    if (command === "who") return { status: 0, stdout: "user-a\n", stderr: "" };
    if (command === "uptime") return { status: 0, stdout: "up 1 day\n", stderr: "" };
    if (command === "tail") {
      const path = args[args.length - 1];
      if (path.endsWith("/var/run/reboot-required")) {
        return { status: 0, stdout: "*** reboot required ***\n", stderr: "" };
      }
      if (path.endsWith("/var/log/audit/audit.log")) {
        return { status: 0, stdout: "audit-a\n", stderr: "" };
      }
      if (path.endsWith("/var/log/apt/history.log")) {
        return { status: 0, stdout: "apt-history-a\n", stderr: "" };
      }
      if (path.endsWith("/var/log/unattended-upgrades/unattended-upgrades.log")) {
        return { status: 0, stdout: "unattended-a\n", stderr: "" };
      }
      return { status: 0, stdout: "cloud-a\n", stderr: "" };
    }
    if (command === "curl") {
      const url = args[args.length - 1];
      if (url.endsWith("/instance-id")) return { status: 0, stdout: "i-bp1test\n", stderr: "" };
      if (url.endsWith("/instance/spot/termination-time")) {
        return { status: 0, stdout: "2026-11-19T10:38:20Z\n", stderr: "" };
      }
      return { status: 0, stdout: "", stderr: "" };
    }
    return { status: 0, stdout: "", stderr: "" };
  };

  const diagnostics = buildSigtermDiagnostics({
    exec: fakeExec,
    env: {
      GITHUB_RUN_ID: "44506476534",
      GITHUB_JOB: "execute (case-a)",
      GITHUB_WORKFLOW: "eval-shard",
      RUNNER_NAME: "spot-f772bf95-test",
      RUNNER_ID: "spot-f772bf95-test",
    },
    now: () => new Date("2026-11-19T10:38:38.888Z"),
    hostname: "iZbp-test",
  });

  assert.equal(diagnostics.status, "sigterm_diagnostics");
  assert.equal(diagnostics.signal, "SIGTERM");
  assert.equal(diagnostics.received_at, "2026-11-19T10:38:38.888Z");
  assert.equal(diagnostics.hostname, "iZbp-test");
  assert.equal(diagnostics.instance_id, "i-bp1test");
  assert.equal(diagnostics.spot_termination_time, "2026-11-19T10:38:20Z");
  assert.equal(diagnostics.systemd.SystemState, "running");
  assert.deepEqual(diagnostics.journal_tail, ["line-a", "line-b"]);
  assert.ok(diagnostics.system_journal_tail.some((line) => line.includes("Stopping github-runner")));
  assert.ok(diagnostics.reboot_candidates.some((line) => line.includes("Reboot requested")));
  assert.ok(diagnostics.previous_boot_journal_tail.some((line) => line.includes("unattended-upgrades")));
  assert.deepEqual(diagnostics.boot_list, ["-1 boot-a", "0 boot-b"]);
  assert.deepEqual(diagnostics.kernel_tail, ["kernel-a"]);
  assert.deepEqual(diagnostics.timers, ["apt-daily.timer timer-a", "apt-daily-upgrade.timer timer-b"]);
  assert.deepEqual(diagnostics.dmesg_tail, ["dmesg-a"]);
  assert.equal(diagnostics.github.run_id, "44506476534");
  assert.equal(diagnostics.github.runner_name, "spot-f772bf95-test");
  assert.deepEqual(diagnostics.auth_log_tail, ["cloud-a"]);
  assert.deepEqual(diagnostics.syslog_tail, ["cloud-a"]);
  assert.deepEqual(diagnostics.boot_journal_tail, ["line-a", "line-b"]);
  assert.deepEqual(diagnostics.apt_history_tail, ["apt-history-a"]);
  assert.deepEqual(diagnostics.unattended_upgrades_tail, ["unattended-a"]);
  assert.deepEqual(diagnostics.audit_log_tail, ["audit-a"]);
  assert.deepEqual(diagnostics.reboot_required, ["*** reboot required ***"]);

  // Aliyun spot termination time lives under instance/spot/...; the bare
  // spot/... path only returns the metadata 404 page.
  const metadataCalls = calls.filter((call) => call[0] === "curl").map((call) => call.at(-1));
  assert.ok(metadataCalls.some((url) => url.endsWith("/instance/spot/termination-time")));
  assert.ok(!metadataCalls.some((url) => url === "http://100.100.100.200/latest/meta-data/spot/termination-time"));
});

test("buildSigtermDiagnostics degrades when a probe fails", () => {
  const fakeExec = (command, args) => {
    if (command === "systemctl") throw new Error("systemd is stopping");
    if (command === "curl") {
      const url = args[args.length - 1];
      if (url.endsWith("/instance-id")) return { status: 28, stdout: "", stderr: "timeout" };
    }
    return { status: 0, stdout: "", stderr: "" };
  };

  const diagnostics = buildSigtermDiagnostics({
    exec: fakeExec,
    env: {},
    now: () => new Date("2026-11-19T10:38:38Z"),
    hostname: "host",
  });

  assert.deepEqual(diagnostics.systemd, {});
  assert.match(diagnostics.commands.systemd.error, /systemd is stopping/);
  assert.equal(diagnostics.instance_id, null);
  assert.equal(diagnostics.spot_termination_time, null);
  assert.deepEqual(diagnostics.reboot_candidates, []);
  assert.deepEqual(diagnostics.apt_history_tail, []);
});
