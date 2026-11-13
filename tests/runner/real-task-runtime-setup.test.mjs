import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import test from "node:test";

import { createScenario, repoRoot, runNode, taskSecret } from "./scenario-helpers.mjs";

/**
 * M8-10: CLI-generated Agent assets declare runtime prerequisites (CLI
 * install + init) that the platform freezes inside
 * `asset_snapshot.runtime_setup`.
 *
 * M8-11: at eval time the runner executes only `install_command` — `init`
 * would create `.claude` as the `eval` user and collide with the host-side
 * materialization of the frozen snapshot (EACCES on `.claude/agents`). The
 * frozen asset snapshot is the sole source of `.claude`: it is materialized
 * after the install, so it stays authoritative. A failing install aborts the
 * case with an explicit error code and trace diagnostic instead of running the
 * agent against an image without the CLI.
 */

const script = join(repoRoot, "runner", "scripts", "real-task.mjs");
const CASE_RUN_ID = "ecr_real";
// Commands mirror the backend load test: user-scoped install under $HOME, then
// the CLI invoked through the same $HOME in init. The init command must never
// reach the eval container any more.
const INSTALL_COMMAND = 'npm install --prefix "$HOME" --no-audit --no-fund @example/openspec-cli';
const INIT_COMMAND = '"$HOME/node_modules/.bin/openspec-cli" init --yes';
const LEGACY_INSTALL = 'npm install --prefix "$HOME" @example/legacy-cli';
const LEGACY_INIT = '"$HOME/node_modules/.bin/legacy-cli" init';
const SETUP_IDENTITY = "-u eval -e HOME=/home/eval -w /testbed";
const CHOWN_COMMAND = "chown -R eval:eval /testbed/.claude";

const cases = [{ case_run_id: CASE_RUN_ID, case_key: "case-real", dataset_case_id: "case-real" }];

function tasksPayload({ runtimeSetup, runtimePrerequisites, assetSnapshot = {}, egressProxy = null } = {}) {
  return {
    model: { name: "claude-sonnet-4", anthropic_base_url: "http://127.0.0.1:9" },
    adapter_key: "swebench",
    runtime: { key: "claude-code" },
    environment_image: "eval/swebench:test",
    egress_proxy: egressProxy,
    asset_snapshot: {
      snapshot_id: "snap-runtime",
      asset_count: 0,
      // The platform always sends the key; an empty object means "no setup".
      runtime_setup: {},
      ...assetSnapshot,
      ...(runtimeSetup ? { runtime_setup: runtimeSetup } : {}),
      ...(runtimePrerequisites ? { runtime_prerequisites: runtimePrerequisites } : {}),
    },
    cases: [
      {
        case_run_id: CASE_RUN_ID,
        case_key: "case-real",
        repo: "example/repo",
        base_commit: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        prompt: "run the generated CLI workflow",
        environment_image: "eval/swebench:test",
        test_patch: "",
        expected_metrics: { fail_to_pass: ["tests/test_bug.py::test_fix"], pass_to_pass: [] },
      },
    ],
  };
}

/** A frozen `.claude` bundle: one command whose content must win over setup. */
function writeFrozenBundle(dir) {
  const files = { "commands/openspec-propose.md": "# frozen command\n" };
  const entries = Object.keys(files)
    .sort()
    .map((path) => ({
      path,
      sha256: `sha256:${createHash("sha256").update(files[path]).digest("hex")}`,
      size: Buffer.byteLength(files[path]),
      mode: "100644",
      content_base64: Buffer.from(files[path]).toString("base64"),
    }));
  const treeDigest = `sha256:${createHash("sha256")
    .update(entries.map((item) => `${item.path}\0${item.sha256}`).join("\n"))
    .digest("hex")}`;
  const document = {
    schema_version: "1.0",
    snapshot_id: "snap-frozen",
    resolved_commit: "c".repeat(40),
    tree_digest: treeDigest,
    files: entries,
  };
  const gzip = gzipSync(Buffer.from(JSON.stringify(document)));
  const bundlePath = join(dir, "asset-bundle.json.gz");
  writeFileSync(bundlePath, gzip);
  return {
    snapshot_id: "snap-frozen",
    resolved_commit: "c".repeat(40),
    tree_digest: treeDigest,
    asset_count: 1,
    bundle_url: `file://${bundlePath}`,
    bundle_digest: `sha256:${createHash("sha256").update(gzip).digest("hex")}`,
  };
}

/**
 * Fake `docker` binary that records every invocation (one line per call).
 * It simulates the three things the assertions need:
 * - a setup command writing `.claude` in the bind-mounted workspace
 *   (`FAKE_WRITE_COMMAND`), and
 * - the agent seeing (and recording) the workspace exactly when it starts.
 * `FAKE_FAIL_COMMAND` makes the matching command exit non-zero.
 */
function installFakeBin() {
  const dir = mkdtempSync(join(tmpdir(), "fake-docker-runtime-setup-"));
  const docker = `#!/bin/sh
dir="$(dirname "$0")"
if [ -n "\${FAKE_DOCKER_LOG:-}" ]; then
  printf '%s\\n' "$(printf '%s' "$*" | tr '\\n' ' ')" >> "$FAKE_DOCKER_LOG"
fi
if [ -n "\${FAKE_WRITE_COMMAND:-}" ]; then
  case "$*" in
    *"$FAKE_WRITE_COMMAND"*)
      mkdir -p "$(cat "$dir/host")/.claude/commands"
      printf 'generated by setup\\n' > "$(cat "$dir/host")/.claude/commands/openspec-propose.md"
      printf 'setup-only\\n' > "$(cat "$dir/host")/.claude/commands/setup-only.md"
      printf 'setup-wrote:%s\\n' "$(cat "$(cat "$dir/host")/.claude/commands/openspec-propose.md")" \\
        >> "\${FAKE_DOCKER_LOG}"
      ;;
  esac
fi
case "$*" in
  "create "*) echo fake-container; exit 0 ;;
esac
if [ -n "\${FAKE_FAIL_COMMAND:-}" ]; then
  case "$*" in
    *"$FAKE_FAIL_COMMAND"*) echo "simulated setup failure" >&2; exit 1 ;;
  esac
fi
case "$*" in
  *"claude-code-smoke.mjs"*)
    host="$(cat "$dir/host")"
    printf 'agent-sees:%s|setup-only:%s\\n' \\
      "$(cat "$host/.claude/commands/openspec-propose.md" 2>/dev/null || echo MISSING)" \\
      "$([ -f "$host/.claude/commands/setup-only.md" ] && echo PRESENT || echo MISSING)" \\
      >> "\${FAKE_DOCKER_LOG}"
    exit 0
    ;;
  *"git"*" diff --cached"*) printf '%s\\n' "diff --git a/app.py b/app.py" "--- a/app.py" "+++ b/app.py" "+fixed"; exit 0 ;;
  *"claude --version"*) echo "2.1.278 (Claude Code)"; exit 0 ;;
esac
host=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "-v" ]; then
    case "$arg" in
      *:/testbed) host="\${arg%:/testbed}" ;;
    esac
  fi
  prev="$arg"
done
case "$*" in
  *"-d --name"*) printf '%s' "$host" > "$dir/host" ;;
esac
exit 0
`;
  const git = "#!/bin/sh\nexit 0\n";
  for (const [name, body] of [["docker", docker], ["git", git]]) {
    const file = join(dir, name);
    writeFileSync(file, body, { mode: 0o755 });
    chmodSync(file, 0o755);
  }
  return dir;
}

const caseArgs = (ctx) => [
  "--dispatch", ctx.dispatchPath,
  "--case", CASE_RUN_ID,
  "--state-dir", ctx.stateDir,
  "--object-store-root", ctx.objectRoot,
];

function runCase(ctx, fakeBin, logPath, env = {}) {
  return runNode(script, caseArgs(ctx), {
    RUNNER_TASK_TOKEN: ctx.token,
    RUNNER_TASK_SECRET: taskSecret,
    RUNNER_HEARTBEAT_DISABLE: "1",
    PATH: `${fakeBin}:${process.env.PATH}`,
    FAKE_DOCKER_LOG: logPath,
    ...env,
  });
}

const invocations = (logPath) =>
  readFileSync(logPath, "utf8").split("\n").filter((line) => line.length > 0);

const indexOf = (lines, needle) => {
  const index = lines.findIndex((line) => line.includes(needle));
  assert.notEqual(index, -1, `missing docker invocation containing ${needle}\n${lines.join("\n")}`);
  return index;
};

const stderrEvents = (result) =>
  result.stderr
    .split("\n")
    .flatMap((line) => {
      try {
        return [JSON.parse(line)];
      } catch {
        return [];
      }
    });

const requestsFor = (ctx, action) =>
  ctx.platform.requests.filter((request) => request.path === `/runner/v1/case-runs/${CASE_RUN_ID}/${action}`);

test("runtime_setup runs only install, with the eval identity/HOME, before the agent", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload({ runtimeSetup: { install_command: INSTALL_COMMAND, init_command: INIT_COMMAND } }),
      modelToken: "model-token-test",
    },
  });
  try {
    const result = await runCase(ctx, fakeBin, logPath);
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    const adapterInstall = indexOf(lines, "npm install -g @anthropic-ai/claude-code");
    const install = indexOf(lines, INSTALL_COMMAND);
    const agent = indexOf(lines, "claude-code-smoke.mjs");
    assert.ok(adapterInstall < install, "asset install must run after the runtime adapter install");
    assert.ok(install < agent, "the asset install must precede the agent run");
    // M8-11: the payload still carries an init command; the runner must not
    // execute it inside the eval container.
    assert.ok(!lines.some((line) => line.includes(INIT_COMMAND)), "init must never run at eval time");

    // The install runs in the mounted workspace, as the same $HOME owner the
    // load test used, so `npm install --prefix "$HOME"` is found by the agent.
    assert.ok(lines[install].includes(`exec ${SETUP_IDENTITY} `), lines[install]);

    const event = stderrEvents(result).find((item) => item.status === "runtime_setup");
    assert.ok(event, `missing runtime_setup event\n${result.stderr}`);
    assert.equal(event.install, true);
    assert.equal(event.init, false);
    assert.equal(event.snapshot_id, "snap-runtime");

    const completed = requestsFor(ctx, "completed");
    assert.equal(completed.length, 1);
    assert.equal(completed[0].body.execution_status, "succeeded");
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("an empty runtime_setup keeps the old behaviour and runs no setup", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const ctx = await createScenario({ cases, behavior: { tasks: tasksPayload(), modelToken: "model-token-test" } });
  try {
    const result = await runCase(ctx, fakeBin, logPath);
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    assert.ok(lines.some((line) => line.includes("claude-code-smoke.mjs")), "agent must still run");
    assert.ok(!lines.some((line) => line.includes(INSTALL_COMMAND)), "no setup install expected");
    assert.ok(!lines.some((line) => line.includes(INIT_COMMAND)), "no setup init expected");
    assert.equal(stderrEvents(result).some((item) => item.status === "runtime_setup"), false);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("an init-only payload runs no setup: empty install means no setup", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload({ runtimeSetup: { init_command: INIT_COMMAND } }),
      modelToken: "model-token-test",
    },
  });
  try {
    const result = await runCase(ctx, fakeBin, logPath);
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    assert.ok(lines.some((line) => line.includes("claude-code-smoke.mjs")), "agent must still run");
    assert.ok(!lines.some((line) => line.includes(INIT_COMMAND)), "init must never run at eval time");
    assert.equal(stderrEvents(result).some((item) => item.status === "runtime_setup"), false);
    assert.equal(stderrEvents(result).some((item) => item.status === "runtime_setup_failed"), false);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("the frozen asset snapshot is materialized after install and wins", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const frozen = writeFrozenBundle(fakeBin);
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload({
        runtimeSetup: { install_command: INSTALL_COMMAND, init_command: INIT_COMMAND },
        assetSnapshot: frozen,
      }),
      modelToken: "model-token-test",
    },
  });
  try {
    // Make the install command write a conflicting `.claude` tree: the frozen
    // snapshot must still be injected after it and replace/remove those files.
    const result = await runCase(ctx, fakeBin, logPath, { FAKE_WRITE_COMMAND: INSTALL_COMMAND });
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    const install = indexOf(lines, INSTALL_COMMAND);
    const chown = indexOf(lines, CHOWN_COMMAND);
    const agent = indexOf(lines, "claude-code-smoke.mjs");
    const agentSees = lines.find((line) => line.startsWith("agent-sees:"));
    assert.ok(agentSees, `agent never started\n${lines.join("\n")}`);
    assert.ok(install < chown && chown < agent, "install -> materialize/chown -> agent");
    // The install did write its own `.claude` first.
    assert.ok(
      lines.some((line) => line === "setup-wrote:generated by setup"),
      `the install did not write the workspace\n${lines.join("\n")}`,
    );
    // Frozen bundle wins: the install-written file is replaced and its extra
    // file is removed by materialization. Both prove install -> materialize.
    assert.match(agentSees, /agent-sees:# frozen command\|setup-only:MISSING/);
    // And the init command was never executed, so it cannot fight the snapshot.
    assert.ok(!lines.some((line) => line.includes(INIT_COMMAND)), "init must never run at eval time");

    const event = stderrEvents(result).find((item) => item.status === "assets_materialized");
    assert.ok(event, `missing assets_materialized event\n${result.stderr}`);
    assert.equal(event.snapshot_id, "snap-frozen");
    assert.equal(event.files, 1);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("a failing install aborts the case with asset_runtime_setup_failed", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload({ runtimeSetup: { install_command: INSTALL_COMMAND, init_command: INIT_COMMAND } }),
      modelToken: "model-token-test",
      uploadUrls: true,
    },
  });
  try {
    const result = await runCase(ctx, fakeBin, logPath, {
      FAKE_FAIL_COMMAND: INSTALL_COMMAND,
      RUNNER_PRESIGN_UPLOADS: "1",
    });
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    assert.ok(lines.some((line) => line.includes(INSTALL_COMMAND)), "install must be attempted");
    assert.ok(!lines.some((line) => line.includes(INIT_COMMAND)), "init must not run after a failed install");
    assert.ok(!lines.some((line) => line.includes(CHOWN_COMMAND)), "no snapshot materialization chown expected");
    assert.ok(!lines.some((line) => line.includes("claude-code-smoke.mjs")), "the agent must not run");

    const failureEvent = stderrEvents(result).find((item) => item.status === "runtime_setup_failed");
    assert.ok(failureEvent, `missing runtime_setup_failed event\n${result.stderr}`);
    assert.equal(failureEvent.error_code, "asset_runtime_setup_failed");
    assert.equal(failureEvent.phase, "install");

    const completed = requestsFor(ctx, "completed");
    assert.equal(completed.length, 1);
    assert.equal(completed[0].body.execution_status, "failed");
    assert.equal(completed[0].body.result_status, "not_scored");
    assert.ok(
      (completed[0].body.trace_manifest?.objects ?? []).some(
        (object) => object.logical_path === "diagnostics/runtime-setup-failure.json",
      ),
      "the setup failure diagnostic must be part of the trace",
    );

    const score = JSON.parse(ctx.platform.objects.get(completed[0].body.score_manifest.object_key).toString("utf8"));
    assert.equal(score["case-real"].error, "asset_runtime_setup_failed");
    assert.equal(score["case-real"].resolved, false);
    assert.match(score["case-real"].message, /simulated setup failure/);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("the legacy runtime_prerequisites field still drives an install-only setup", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload({
        runtimePrerequisites: { install_command: LEGACY_INSTALL, init_command: LEGACY_INIT },
      }),
      modelToken: "model-token-test",
    },
  });
  try {
    const result = await runCase(ctx, fakeBin, logPath);
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    const install = indexOf(lines, LEGACY_INSTALL);
    const agent = indexOf(lines, "claude-code-smoke.mjs");
    assert.ok(install < agent, "legacy setup must run before the agent");
    assert.ok(!lines.some((line) => line.includes(LEGACY_INIT)), "legacy init must never run at eval time");
    assert.ok(lines[install].includes(`exec ${SETUP_IDENTITY} `), lines[install]);

    const event = stderrEvents(result).find((item) => item.status === "runtime_setup");
    assert.ok(event, `missing runtime_setup event\n${result.stderr}`);
    assert.equal(event.install, true);
    assert.equal(event.init, false);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

// ---------------------------------------------------------------------------
// M8-14: GitHub egress proxy injection
// ---------------------------------------------------------------------------

const EGRESS_PROXY = "http://10.0.0.10:3128";

function proxyEnvFrom(line) {
  const values = {};
  const matches = line.matchAll(/-e (HTTPS_PROXY|HTTP_PROXY|NO_PROXY)=([^ ]*)/g);
  for (const match of matches) values[match[1]] = match[2];
  return values;
}

test("the egress proxy env reaches the asset install and the agent exec", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const tasks = tasksPayload({
    runtimeSetup: { install_command: INSTALL_COMMAND, init_command: INIT_COMMAND },
    egressProxy: EGRESS_PROXY,
  });
  // Distinct platform/object-storage hosts prove NO_PROXY is derived from the
  // payload rather than hard-coded.
  tasks.model.anthropic_base_url = "http://model-gateway.vpc:8000/model-gateway/anthropic";
  tasks.asset_snapshot.bundle_url = "https://bucket.oss-cn-hangzhou.aliyuncs.com/asset-bundle.json.gz";
  const ctx = await createScenario({
    cases,
    behavior: { tasks, modelToken: "model-token-test" },
  });
  try {
    const result = await runCase(ctx, fakeBin, logPath);
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    const install = lines[indexOf(lines, INSTALL_COMMAND)];
    const agent = lines[indexOf(lines, "claude-code-smoke.mjs")];
    for (const line of [install, agent]) {
      const env = proxyEnvFrom(line);
      assert.equal(env.HTTPS_PROXY, EGRESS_PROXY, line);
      assert.equal(env.HTTP_PROXY, EGRESS_PROXY, line);
      const noProxy = new Set((env.NO_PROXY ?? "").split(","));
      for (const host of [
        "localhost",
        "127.0.0.1",
        "::1",
        "registry.npmmirror.com",
        "registry.npmjs.org",
        "model-gateway.vpc",
        "bucket.oss-cn-hangzhou.aliyuncs.com",
      ]) {
        assert.ok(noProxy.has(host), `NO_PROXY is missing ${host}: ${line}`);
      }
    }
    // The existing exec identity/order is preserved; proxy flags come after it.
    assert.ok(install.includes(`exec ${SETUP_IDENTITY} `), install);
    assert.ok(agent.startsWith("exec -u eval -e HOME=/home/eval -e WORKSPACE=/testbed "), agent);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});

test("no proxy configuration leaves install and agent exec unchanged", async () => {
  const fakeBin = installFakeBin();
  const logPath = join(fakeBin, "docker.log");
  const ctx = await createScenario({
    cases,
    behavior: {
      tasks: tasksPayload({ runtimeSetup: { install_command: INSTALL_COMMAND, init_command: INIT_COMMAND } }),
      modelToken: "model-token-test",
    },
  });
  try {
    const result = await runCase(ctx, fakeBin, logPath);
    assert.equal(result.code, 0, result.stderr);

    const lines = invocations(logPath);
    const install = lines[indexOf(lines, INSTALL_COMMAND)];
    const agent = lines[indexOf(lines, "claude-code-smoke.mjs")];
    for (const line of [install, agent]) {
      assert.equal(proxyEnvFrom(line).HTTPS_PROXY, undefined, line);
      assert.equal(line.includes("-e HTTP_PROXY="), false, line);
      assert.equal(line.includes("-e NO_PROXY="), false, line);
    }
    assert.ok(install.includes(`exec ${SETUP_IDENTITY} `), install);
  } finally {
    rmSync(fakeBin, { recursive: true, force: true });
    await ctx.close();
  }
});
