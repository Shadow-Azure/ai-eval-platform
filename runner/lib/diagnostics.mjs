// SIGTERM diagnostics collected on the runner host.
//
// GitHub gives a self-hosted runner roughly 7.5 seconds between SIGTERM and
// SIGKILL. The collector therefore uses short per-command timeouts and only
// reads the few sources that can identify *who* stopped the runner:
// systemd's state, the runner journal, kernel messages, the last logins and
// the Aliyun instance metadata (including the spot termination time).
//
// The function is pure enough to unit test: pass a fake `exec` and `env`.

import { spawnSync } from "node:child_process";
import os from "node:os";

const COMMAND_TIMEOUT_MS = 500;
const MAX_OUTPUT_CHARS = 4000;

function truncate(value) {
  const text = String(value ?? "");
  return text.length > MAX_OUTPUT_CHARS ? text.slice(-MAX_OUTPUT_CHARS) : text;
}

function run(exec, command, args, timeoutMs = COMMAND_TIMEOUT_MS) {
  try {
    const result = exec(command, args, {
      encoding: "utf8",
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024,
    });
    return {
      status: typeof result?.status === "number" ? result.status : null,
      stdout: truncate(result?.stdout),
      stderr: truncate(result?.stderr),
      error: result?.error ? String(result.error.message || result.error) : null,
    };
  } catch (error) {
    return {
      status: null,
      stdout: "",
      stderr: "",
      error: String(error?.message || error),
    };
  }
}

function lines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function parseSystemd(output) {
  const values = {};
  for (const line of lines(output)) {
    const index = line.indexOf("=");
    if (index > 0) values[line.slice(0, index)] = line.slice(index + 1);
  }
  return values;
}

function metadata(exec, path) {
  const result = run(
    exec,
    "curl",
    ["-sS", "--connect-timeout", "0.35", "--max-time", "0.5", `http://100.100.100.200/latest/meta-data/${path}`],
    650,
  );
  return result.status === 0 ? result.stdout.trim() : "";
}

/**
 * Build a JSON-serializable SIGTERM snapshot.
 *
 * @param {object} [options]
 * @param {Function} [options.exec] spawnSync-compatible executor
 * @param {object} [options.env] environment (defaults to process.env)
 * @param {Function} [options.now] clock (defaults to new Date)
 * @param {string} [options.hostname] host name (defaults to os.hostname)
 */
export function buildSigtermDiagnostics({
  exec = spawnSync,
  env = process.env,
  now = () => new Date(),
  hostname = os.hostname(),
} = {}) {
  const systemdResult = run(exec, "systemctl", [
    "show",
    "github-runner",
    "-p",
    "ActiveState,SubState,Result,ExecMainStatus",
  ]);
  const journalResult = run(exec, "journalctl", [
    "-u",
    "github-runner",
    "-n",
    "50",
    "--no-pager",
  ]);
  const dmesgResult = run(exec, "dmesg", [], 400);
  const lastResult = run(exec, "last", ["-x", "-n", "20"], 300);
  const whoResult = run(exec, "who", [], 200);
  const uptimeResult = run(exec, "uptime", [], 200);
  const cloudInitResult = run(exec, "tail", ["-n", "50", "/var/log/cloud-init-output.log"], 300);

  const instanceId = metadata(exec, "instance-id");
  const terminationTime = metadata(exec, "spot/termination-time");

  return {
    status: "sigterm_diagnostics",
    received_at: now().toISOString(),
    signal: "SIGTERM",
    pid: process.pid,
    ppid: process.ppid,
    hostname,
    instance_id: instanceId || null,
    spot_termination_time: terminationTime || null,
    github: {
      run_id: env.GITHUB_RUN_ID ?? null,
      job: env.GITHUB_JOB ?? null,
      workflow: env.GITHUB_WORKFLOW ?? null,
      runner_name: env.RUNNER_NAME ?? null,
      runner_id: env.RUNNER_ID ?? null,
    },
    systemd: parseSystemd(systemdResult.stdout),
    journal_tail: lines(journalResult.stdout).slice(-30),
    dmesg_tail: lines(dmesgResult.stdout).slice(-30),
    last_logins: lines(lastResult.stdout).slice(-20),
    who: lines(whoResult.stdout),
    uptime: uptimeResult.stdout.trim() || null,
    cloud_init_tail: lines(cloudInitResult.stdout).slice(-30),
    commands: {
      systemd: { status: systemdResult.status, error: systemdResult.error },
      journal: { status: journalResult.status, error: journalResult.error },
      dmesg: { status: dmesgResult.status, error: dmesgResult.error },
      metadata_instance_id: { value: instanceId || null },
      metadata_termination_time: { value: terminationTime || null },
    },
  };
}
