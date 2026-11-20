// SIGTERM diagnostics collected on the runner host.
//
// GitHub gives a self-hosted runner roughly 7.5 seconds between SIGTERM and
// SIGKILL. The collector therefore uses short per-command timeouts and only
// reads the sources that can identify *who* stopped the runner: systemd's
// state, the full system journal before the stop, apt/unattended-upgrades and
// audit tails, kernel messages, the last logins and the Aliyun instance
// metadata (including the spot termination time).
//
// The function is pure enough to unit test: pass a fake `exec` and `env`.

import { spawnSync } from "node:child_process";
import os from "node:os";

const COMMAND_TIMEOUT_MS = 500;
const MAX_OUTPUT_CHARS = 4000;
const REBOOT_LINE_PATTERN =
  /(reboot|shutdown|power.?off|power.?on|systemd-logind|systemd-shutdownd|apt|unattended|cloud-init|systemctl|Stopping github-runner|SIGTERM|signal|panic|oom|watchdog)/i;

function truncate(value, maxChars = MAX_OUTPUT_CHARS) {
  const text = String(value ?? "");
  return text.length > maxChars ? text.slice(-maxChars) : text;
}

function run(exec, command, args, timeoutMs = COMMAND_TIMEOUT_MS, maxChars = MAX_OUTPUT_CHARS) {
  try {
    const result = exec(command, args, {
      encoding: "utf8",
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024,
    });
    return {
      status: typeof result?.status === "number" ? result.status : null,
      stdout: truncate(result?.stdout, maxChars),
      stderr: truncate(result?.stderr, maxChars),
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

function relevantLines(text, limit) {
  const all = lines(text);
  const hits = all.filter((line) => REBOOT_LINE_PATTERN.test(line));
  return (hits.length > 0 ? hits : all).slice(-limit);
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

function commandSummary(result) {
  return { status: result.status, error: result.error };
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
    "ActiveState,SubState,Result,ExecMainStatus,SystemState",
  ]);
  const journalResult = run(exec, "journalctl", [
    "-u",
    "github-runner",
    "-n",
    "100",
    "--no-pager",
  ]);
  // The unit journal alone cannot show whether systemd entered shutdown on its
  // own. Capture the full journal around the stop (including logind, apt,
  // unattended-upgrades and cloud-init lines) and the previous boot when the
  // system already restarted.
  const systemJournalResult = run(
    exec,
    "journalctl",
    ["-b", "-n", "600", "--no-pager", "-o", "short-precise"],
    600,
    20000,
  );
  const previousBootResult = run(
    exec,
    "journalctl",
    ["-b", "-1", "-n", "300", "--no-pager", "-o", "short-precise"],
    450,
    12000,
  );
  const bootListResult = run(exec, "journalctl", ["--list-boots", "--no-pager"], 250);
  const kernelResult = run(exec, "journalctl", ["-k", "-b", "-n", "150", "--no-pager"], 300);
  const timersResult = run(exec, "systemctl", ["list-timers", "--all", "--no-pager"], 300);
  const dmesgResult = run(exec, "dmesg", [], 300);
  const lastResult = run(exec, "last", ["-x", "-n", "20"], 250);
  const whoResult = run(exec, "who", [], 150);
  const uptimeResult = run(exec, "uptime", [], 150);
  // A local `systemctl stop` / SSH / sudo is not in Aliyun ActionTrail. These
  // tails are the only remaining place to see who touched the runner unit.
  const authResult = run(exec, "tail", ["-n", "100", "/var/log/auth.log"], 250);
  const syslogResult = run(exec, "tail", ["-n", "100", "/var/log/syslog"], 250);
  const bootResult = run(exec, "journalctl", ["-b", "-n", "100", "--no-pager"], 400);
  const aptHistoryResult = run(exec, "tail", ["-n", "80", "/var/log/apt/history.log"], 250);
  const aptTermResult = run(exec, "tail", ["-n", "40", "/var/log/apt/term.log"], 250);
  const unattendedResult = run(
    exec,
    "tail",
    ["-n", "80", "/var/log/unattended-upgrades/unattended-upgrades.log"],
    250,
  );
  const auditResult = run(exec, "tail", ["-n", "120", "/var/log/audit/audit.log"], 300);
  const rebootRequiredResult = run(exec, "tail", ["-n", "8", "/var/run/reboot-required"], 150);
  const cloudInitOutputResult = run(
    exec,
    "tail",
    ["-n", "50", "/var/log/cloud-init-output.log"],
    250,
  );
  const cloudInitLogResult = run(exec, "tail", ["-n", "80", "/var/log/cloud-init.log"], 250);
  // M8 #348: name the auto-update path that can stop a runner. The masked
  // units and APT periodic policy prove whether this instance was booted from
  // a hardened image; needrestart and the image record show the rest.
  const autoUpdateUnitsResult = run(
    exec,
    "systemctl",
    [
      "is-enabled",
      "apt-daily.timer",
      "apt-daily-upgrade.timer",
      "apt-daily.service",
      "apt-daily-upgrade.service",
      "unattended-upgrades.service",
      "ua-timer.timer",
    ],
    250,
  );
  const aptPeriodicResult = run(exec, "apt-config", ["dump"], 250);
  const needrestartResult = run(
    exec,
    "cat",
    ["/etc/needrestart/conf.d/99-eval-no-restart.conf"],
    150,
  );
  const runnerImageResult = run(exec, "cat", ["/etc/eval/runner-image.json"], 150);

  const instanceId = metadata(exec, "instance-id");
  // Aliyun exposes the spot termination time under instance/spot/...; the
  // bare spot/... path (used before M8) always returns the metadata 404 page.
  const terminationTime = metadata(exec, "instance/spot/termination-time");

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
    system_journal_tail: lines(systemJournalResult.stdout).slice(-80),
    reboot_candidates: relevantLines(systemJournalResult.stdout, 100),
    previous_boot_journal_tail: relevantLines(previousBootResult.stdout, 80),
    boot_list: lines(bootListResult.stdout).slice(-10),
    kernel_tail: lines(kernelResult.stdout).slice(-60),
    timers: lines(timersResult.stdout).slice(-20),
    dmesg_tail: lines(dmesgResult.stdout).slice(-30),
    last_logins: lines(lastResult.stdout).slice(-20),
    who: lines(whoResult.stdout),
    uptime: uptimeResult.stdout.trim() || null,
    cloud_init_tail: lines(cloudInitOutputResult.stdout).slice(-30),
    cloud_init_log_tail: lines(cloudInitLogResult.stdout).slice(-40),
    auth_log_tail: lines(authResult.stdout).slice(-50),
    syslog_tail: lines(syslogResult.stdout).slice(-50),
    boot_journal_tail: lines(bootResult.stdout).slice(-50),
    apt_history_tail: lines(aptHistoryResult.stdout).slice(-40),
    apt_term_tail: lines(aptTermResult.stdout).slice(-20),
    unattended_upgrades_tail: lines(unattendedResult.stdout).slice(-40),
    audit_log_tail: lines(auditResult.stdout).slice(-60),
    reboot_required: lines(rebootRequiredResult.stdout).slice(-5),
    auto_update_units: lines(autoUpdateUnitsResult.stdout).slice(-10),
    apt_periodic: lines(aptPeriodicResult.stdout)
      .filter((line) =>
        /^APT::Periodic::(Update-Package-Lists|Download-Upgradeable-Packages|AutocleanInterval|Unattended-Upgrade)/.test(
          line,
        ),
      )
      .slice(-10),
    needrestart: lines(needrestartResult.stdout).slice(-10),
    runner_image: lines(runnerImageResult.stdout).slice(-20),
    commands: {
      systemd: commandSummary(systemdResult),
      journal: commandSummary(journalResult),
      system_journal: commandSummary(systemJournalResult),
      previous_boot_journal: commandSummary(previousBootResult),
      boot_list: commandSummary(bootListResult),
      kernel_journal: commandSummary(kernelResult),
      timers: commandSummary(timersResult),
      dmesg: commandSummary(dmesgResult),
      auth_log: commandSummary(authResult),
      syslog: commandSummary(syslogResult),
      boot_journal: commandSummary(bootResult),
      apt_history: commandSummary(aptHistoryResult),
      apt_term: commandSummary(aptTermResult),
      unattended_upgrades: commandSummary(unattendedResult),
      audit_log: commandSummary(auditResult),
      reboot_required: commandSummary(rebootRequiredResult),
      auto_update_units: commandSummary(autoUpdateUnitsResult),
      apt_periodic: commandSummary(aptPeriodicResult),
      needrestart: commandSummary(needrestartResult),
      runner_image: commandSummary(runnerImageResult),
      cloud_init_output: commandSummary(cloudInitOutputResult),
      cloud_init_log: commandSummary(cloudInitLogResult),
      metadata_instance_id: { value: instanceId || null },
      metadata_termination_time: { value: terminationTime || null },
    },
  };
}
