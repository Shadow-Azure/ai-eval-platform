import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");
const bootstrapPath = join(repoRoot, "infra", "runner", "bootstrap.sh");
const agentSource = join(repoRoot, "runner", "agent", "heartbeat.mjs");

function makeStage() {
  const root = mkdtempSync(join(tmpdir(), "runner-bootstrap-"));
  const bin = join(root, "bin");
  mkdirSync(bin, { recursive: true });
  const log = join(root, "systemctl.log");
  writeFileSync(
    join(bin, "systemctl"),
    [
      "#!/usr/bin/env bash",
      'echo "systemctl $*" >> "$FAKE_SYSTEMCTL_LOG"',
      'case "$1" in',
      "  is-active) echo active; exit 0;;",
      "  *) exit 0;;",
      "esac",
      "",
    ].join("\n"),
    { mode: 0o755 },
  );
  return { root, bin, log, envFile: join(root, "etc", "eval-platform", "runner.env") };
}

function runBootstrap(stage, extraEnv = {}, args = []) {
  const env = {
    ...process.env,
    INSTALL_ROOT: stage.root,
    SYSTEMCTL: "systemctl",
    PATH: `${stage.bin}:${process.env.PATH}`,
    FAKE_SYSTEMCTL_LOG: stage.log,
    NODE_BIN: process.execPath,
    PLATFORM_BASE_URL: "http://127.0.0.1:8000",
    RUNNER_HEARTBEAT_TOKEN: "staged-token",
    RUNNER_ID: "fixed-linux-eval",
    RUNNER_NAME: "fixed-linux-eval",
    RUNNER_LABELS: "self-hosted,linux,eval",
    HEARTBEAT_INTERVAL_SECONDS: "30",
    ...extraEnv,
  };
  return execFileSync("bash", [bootstrapPath, ...args], {
    env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function systemctlCalls(stage) {
  if (!existsSync(stage.log)) return [];
  return readFileSync(stage.log, "utf8").trim().split("\n");
}

test("bootstrap script exists and passes bash -n", () => {
  assert.ok(existsSync(bootstrapPath), "infra/runner/bootstrap.sh 缺失");
  execFileSync("bash", ["-n", bootstrapPath], { encoding: "utf8" });
});

test("fresh install stages agent, env file and unit, then enables the service", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  const output = runBootstrap(stage);

  const agentInstalled = join(stage.root, "opt", "ai-eval-platform-runner", "heartbeat.mjs");
  assert.ok(existsSync(agentInstalled), "heartbeat.mjs 未安装");
  assert.equal(
    createHash("sha256").update(readFileSync(agentInstalled)).digest("hex"),
    createHash("sha256").update(readFileSync(agentSource)).digest("hex"),
    "安装的 heartbeat.mjs 与 runner/agent/heartbeat.mjs 不一致",
  );

  const stat = execFileSync("stat", ["-f", "%Lp", stage.envFile], { encoding: "utf8" }).trim();
  assert.equal(stat, "600", "runner.env 必须是 root-only 600");

  const envContent = readFileSync(stage.envFile, "utf8");
  for (const key of [
    "PLATFORM_BASE_URL",
    "RUNNER_HEARTBEAT_TOKEN",
    "RUNNER_ID",
    "RUNNER_NAME",
    "RUNNER_LABELS",
    "HEARTBEAT_INTERVAL_SECONDS",
  ]) {
    assert.match(envContent, new RegExp(`^${key}=`, "m"), `runner.env 缺 ${key}`);
  }
  assert.match(envContent, /^RUNNER_HEARTBEAT_TOKEN=staged-token$/m);

  const unitPath = join(stage.root, "etc", "systemd", "system", "eval-platform-heartbeat.service");
  const unit = readFileSync(unitPath, "utf8");
  assert.match(unit, /EnvironmentFile=\/etc\/eval-platform\/runner\.env/);
  assert.match(unit, /ExecStart=.*node .*heartbeat\.mjs/);
  assert.match(unit, /Restart=always/);

  const calls = systemctlCalls(stage).join("\n");
  assert.match(calls, /daemon-reload/);
  assert.match(calls, /enable.*--now.*eval-platform-heartbeat/);
  assert.match(output, /changed/);
  assert.match(output, /installed/);
});

test("rerun without changes is a no-op (no restart)", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  runBootstrap(stage);
  rmSync(stage.log);
  const output = runBootstrap(stage);

  const calls = systemctlCalls(stage);
  assert.ok(!calls.some((line) => line.includes("restart")), `不应 restart：${calls.join("; ")}`);
  assert.ok(!calls.some((line) => line.includes("daemon-reload")), `不应 daemon-reload：${calls.join("; ")}`);
  assert.match(calls.join("\n"), /is-active/);
  assert.match(output, /unchanged/);
});

test("existing env file keeps keys not provided this run", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));
  mkdirSync(dirname(stage.envFile), { recursive: true });
  writeFileSync(
    stage.envFile,
    [
      "PLATFORM_BASE_URL=http://127.0.0.1:8000",
      "RUNNER_HEARTBEAT_TOKEN=existing-secret",
      "HEARTBEAT_INTERVAL_SECONDS=30",
      "",
    ].join("\n"),
    { mode: 0o600 },
  );

  runBootstrap(stage, { RUNNER_HEARTBEAT_TOKEN: "" });

  const content = readFileSync(stage.envFile, "utf8");
  assert.match(content, /^RUNNER_HEARTBEAT_TOKEN=existing-secret$/m);
});

test("fresh install without RUNNER_HEARTBEAT_TOKEN fails fast", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  assert.throws(
    () => runBootstrap(stage, { RUNNER_HEARTBEAT_TOKEN: "" }),
    /RUNNER_HEARTBEAT_TOKEN/,
  );
});

test("SYNC_AGENT=1 upgrades an existing agent file", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  runBootstrap(stage);
  const agentInstalled = join(stage.root, "opt", "ai-eval-platform-runner", "heartbeat.mjs");
  chmodSync(agentInstalled, 0o644);
  writeFileSync(agentInstalled, "// stale deployed copy\n");

  const output = runBootstrap(stage, { SYNC_AGENT: "1" });
  assert.equal(
    createHash("sha256").update(readFileSync(agentInstalled)).digest("hex"),
    createHash("sha256").update(readFileSync(agentSource)).digest("hex"),
    "SYNC_AGENT=1 应覆盖为 repo 版本",
  );
  assert.match(output, /agent-synced/);
});

test("changed unit content triggers daemon-reload and restart", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  runBootstrap(stage);
  const unitPath = join(stage.root, "etc", "systemd", "system", "eval-platform-heartbeat.service");
  writeFileSync(unitPath, "# drifted unit\n");

  runBootstrap(stage);
  const calls = systemctlCalls(stage).join("\n");
  assert.match(calls, /daemon-reload/);
  assert.match(calls, /restart eval-platform-heartbeat/);
});

test("script never embeds literal credentials", () => {
  const script = readFileSync(bootstrapPath, "utf8");
  // Match a literal token-style value after `=` (not ${...}, not `...`/`<placeholder>`).
  const literal = script.match(/RUNNER_HEARTBEAT_TOKEN=(?!\.\.\.|<|\$\{)[A-Za-z0-9_/.+-]{8,}/);
  assert.ok(!literal, `脚本内出现字面量 token：${literal?.[0]}`);
  assert.match(script, /set -euo pipefail/);
});

test("GITHUB runner registration only runs with RUNNER_INSTALL=1", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  runBootstrap(stage);
  const script = readFileSync(bootstrapPath, "utf8");
  assert.match(script, /RUNNER_INSTALL/, "脚本必须支持显式 RUNNER_INSTALL 门");
  assert.ok(
    !systemctlCalls(stage).some((line) => /actions\.runner/.test(line)),
    "默认运行不得触碰 actions.runner 服务",
  );
});

test("env-only change restarts the service to re-read EnvironmentFile", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  runBootstrap(stage);
  rmSync(stage.log);
  runBootstrap(stage, { PLATFORM_BASE_URL: "http://127.0.0.1:8001" });

  const calls = systemctlCalls(stage).join("\n");
  assert.match(calls, /restart eval-platform-heartbeat/);
  assert.ok(!calls.includes("daemon-reload"), "unit 未变不应 daemon-reload");
});

test("drifted agent file is kept unless SYNC_AGENT=1", (t) => {
  const stage = makeStage();
  t.after(() => rmSync(stage.root, { recursive: true, force: true }));

  runBootstrap(stage);
  const agentInstalled = join(stage.root, "opt", "ai-eval-platform-runner", "heartbeat.mjs");
  writeFileSync(agentInstalled, "// drifted\n");

  const output = runBootstrap(stage);
  assert.equal(readFileSync(agentInstalled, "utf8"), "// drifted\n", "默认应保留主机现场");
  assert.match(output, /unchanged/);
  assert.match(output, /SYNC_AGENT=1/);
});
