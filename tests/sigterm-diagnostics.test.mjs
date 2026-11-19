import assert from "node:assert/strict";
import test from "node:test";

import { buildSigtermDiagnostics } from "../runner/lib/diagnostics.mjs";

test("buildSigtermDiagnostics captures systemd, journal, metadata and GitHub context", () => {
  const calls = [];
  const fakeExec = (command, args) => {
    calls.push([command, ...args]);
    if (command === "systemctl") {
      return {
        status: 0,
        stdout: "ActiveState=deactivating\nSubState=stop-sigterm\nResult=success\nExecMainStatus=0\n",
        stderr: "",
      };
    }
    if (command === "journalctl") {
      return { status: 0, stdout: "line-a\nline-b\n", stderr: "" };
    }
    if (command === "dmesg") return { status: 0, stdout: "dmesg-a\n", stderr: "" };
    if (command === "last") return { status: 0, stdout: "login-a\n", stderr: "" };
    if (command === "who") return { status: 0, stdout: "user-a\n", stderr: "" };
    if (command === "uptime") return { status: 0, stdout: "up 1 day\n", stderr: "" };
    if (command === "tail") return { status: 0, stdout: "cloud-a\n", stderr: "" };
    if (command === "curl") {
      const url = args[args.length - 1];
      if (url.endsWith("/instance-id")) return { status: 0, stdout: "i-bp1test\n", stderr: "" };
      if (url.endsWith("/spot/termination-time")) {
        return { status: 0, stdout: "2026-11-19T10:38:20Z\n", stderr: "" };
      }
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
  assert.equal(diagnostics.systemd.ActiveState, "deactivating");
  assert.equal(diagnostics.systemd.SubState, "stop-sigterm");
  assert.deepEqual(diagnostics.journal_tail, ["line-a", "line-b"]);
  assert.deepEqual(diagnostics.dmesg_tail, ["dmesg-a"]);
  assert.equal(diagnostics.github.run_id, "44506476534");
  assert.equal(diagnostics.github.runner_name, "spot-f772bf95-test");
  assert.deepEqual(diagnostics.auth_log_tail, ["cloud-a"]);
  assert.deepEqual(diagnostics.syslog_tail, ["cloud-a"]);
  assert.deepEqual(diagnostics.boot_journal_tail, ["line-a", "line-b"]);
  assert.ok(calls.some((call) => call[0] === "systemctl"));
  assert.ok(calls.some((call) => call[0] === "journalctl"));
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
});
