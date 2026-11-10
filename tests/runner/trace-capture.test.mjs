import assert from "node:assert/strict";
import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  collectTraceBundle,
  dedupeTraceObjects,
  isSafeLogicalPath,
  readRootSessionId,
  traceManifestFromBundle,
  writeDiagnostic,
} from "../../runner/lib/trace-capture.mjs";
import { runClaudeCode } from "../../runner/lib/runtime-claude-code.mjs";

function workspace() {
  const root = mkdtempSync(join(tmpdir(), "trace-capture-"));
  const workspaceDir = join(root, "workspace");
  const runtimeHome = join(root, "home");
  const traceDir = join(workspaceDir, "trace");
  mkdirSync(join(runtimeHome, ".claude", "projects", "-testbed"), { recursive: true });
  mkdirSync(traceDir, { recursive: true });
  return { root, workspaceDir, runtimeHome, traceDir };
}

function writeStdout(traceDir, sessionId = "s1") {
  const path = join(traceDir, "claude-stream.jsonl");
  writeFileSync(
    path,
    [
      JSON.stringify({ type: "system", session_id: sessionId, model: "MiniMax-M2.7-highspeed" }),
      JSON.stringify({ type: "result", subtype: "success", is_error: false, num_turns: 1 }),
      "",
    ].join("\n"),
  );
  return path;
}

test("collects root session, stdout and stderr with logical paths only", () => {
  const ctx = workspace();
  writeStdout(ctx.traceDir);
  writeFileSync(join(ctx.traceDir, "claude-stderr.log"), "diagnostic\n");
  writeFileSync(
    join(ctx.runtimeHome, ".claude", "projects", "-testbed", "s1.jsonl"),
    JSON.stringify({ type: "user", sessionId: "s1" }) + "\n",
  );

  const bundle = collectTraceBundle({
    runtimeHome: ctx.runtimeHome,
    traceDir: ctx.traceDir,
    runtimeVersion: "2.1.278",
  });
  assert.equal(bundle.capture_status, "complete");
  assert.deepEqual(bundle.incomplete_reasons, []);
  assert.deepEqual(
    bundle.files.map((file) => file.logical_path),
    [
      "diagnostics/stderr.log",
      "diagnostics/stdout.jsonl",
      "native/projects/-testbed/s1.jsonl",
    ],
  );
  const session = bundle.files.find((file) => file.logical_path.endsWith("s1.jsonl"));
  assert.equal(session.artifact_kind, "trace");
  assert.equal(session.runtime_version, "2.1.278");
  assert.equal(session.format_version, "1");
  assert.ok(isSafeLogicalPath(session.logical_path));
  const serialized = JSON.stringify(bundle, (key, value) => (key === "bytes" ? undefined : value));
  assert.equal(serialized.includes(ctx.runtimeHome), false);
  assert.equal(serialized.includes(ctx.workspaceDir), false);
});

test("collects subagent sessions and their .meta.json relation evidence", () => {
  const ctx = workspace();
  writeStdout(ctx.traceDir);
  const project = join(ctx.runtimeHome, ".claude", "projects", "-testbed");
  mkdirSync(join(project, "subagents"), { recursive: true });
  writeFileSync(join(project, "s1.jsonl"), "{}\n");
  writeFileSync(join(project, "subagents", "agent-a1.jsonl"), "{}\n");
  writeFileSync(
    join(project, "subagents", "agent-a1.meta.json"),
    JSON.stringify({ agentId: "a1", toolUseId: "tool-1", spawnDepth: 1 }) + "\n",
  );

  const bundle = collectTraceBundle({ runtimeHome: ctx.runtimeHome, traceDir: ctx.traceDir });
  assert.equal(bundle.capture_status, "complete");
  const meta = bundle.files.find((file) => file.artifact_kind === "trace_metadata");
  assert.ok(meta);
  assert.equal(meta.logical_path, "native/projects/-testbed/subagents/agent-a1.meta.json");
  assert.equal(meta.native_format, "claude-code.subagent.meta+json");
});

test("marks stdout-only and missing subagent evidence as partial", () => {
  const ctx = workspace();
  writeStdout(ctx.traceDir);
  const stdoutOnly = collectTraceBundle({ runtimeHome: ctx.runtimeHome, traceDir: ctx.traceDir });
  assert.equal(stdoutOnly.capture_status, "partial");
  assert.ok(stdoutOnly.incomplete_reasons.includes("native_session_missing"));

  const project = join(ctx.runtimeHome, ".claude", "projects", "-testbed");
  mkdirSync(join(project, "subagents"), { recursive: true });
  writeFileSync(join(project, "s1.jsonl"), "{}\n");
  writeFileSync(join(project, "subagents", "agent-a1.jsonl"), "{}\n");
  const noMeta = collectTraceBundle({ runtimeHome: ctx.runtimeHome, traceDir: ctx.traceDir });
  assert.equal(noMeta.capture_status, "partial");
  assert.ok(noMeta.incomplete_reasons.includes("subagent_metadata_missing"));
});

test("root session id comes from the structured stream, not the file system", () => {
  const ctx = workspace();
  const stdout = writeStdout(ctx.traceDir, "session-xyz");
  assert.equal(readRootSessionId(stdout), "session-xyz");
  writeFileSync(join(ctx.runtimeHome, ".claude", "projects", "-testbed", "other.jsonl"), "{}\n");
  const bundle = collectTraceBundle({ runtimeHome: ctx.runtimeHome, traceDir: ctx.traceDir });
  assert.ok(bundle.incomplete_reasons.includes("root_session_missing"));
});

test("a failed claude run still retains native and diagnostic files", async () => {
  const ctx = workspace();
  const bin = join(ctx.root, "claude");
  writeFileSync(
    bin,
    [
      "#!/usr/bin/env node",
      "const fs = require('node:fs');",
      "const path = require('node:path');",
      "console.log(JSON.stringify({type:'system',session_id:'s1',model:'m'}));",
      "const dir = path.join(process.env.HOME, '.claude', 'projects', 'p');",
      "fs.mkdirSync(path.join(dir, 'subagents'), {recursive:true});",
      "fs.writeFileSync(path.join(dir, 's1.jsonl'), '{\"type\":\"user\"}\\n');",
      "fs.writeFileSync(path.join(dir, 'subagents', 'agent-a1.jsonl'), '{\"agentId\":\"a1\"}\\n');",
      "console.error('gateway exploded');",
      "process.exit(7);",
    ].join("\n"),
  );
  chmodSync(bin, 0o755);
  const result = await runClaudeCode({
    prompt: "fix",
    model: "m",
    workspace: ctx.workspaceDir,
    runtimeHome: ctx.runtimeHome,
    gatewayBaseUrl: "http://127.0.0.1:9",
    gatewayToken: "v1.token.signature",
    command: bin,
  });
  assert.equal(result.exitCode, 7);
  const bundle = collectTraceBundle({ runtimeHome: ctx.runtimeHome, traceDir: ctx.traceDir });
  const paths = bundle.files.map((file) => file.logical_path);
  assert.ok(paths.includes("native/projects/p/s1.jsonl"));
  assert.ok(paths.includes("native/projects/p/subagents/agent-a1.jsonl"));
  assert.ok(paths.includes("diagnostics/stdout.jsonl"));
  assert.ok(paths.includes("diagnostics/stderr.log"));
  assert.ok(bundle.incomplete_reasons.includes("subagent_metadata_missing"));
});

test("dedupe collapses repeated digests and rejects ambiguous paths", () => {
  const repeated = dedupeTraceObjects([
    { artifact_kind: "trace", logical_path: "native/a.jsonl", bytes: Buffer.from("{}\n") },
    { artifact_kind: "trace", logical_path: "native/a.jsonl", bytes: Buffer.from("{}\n") },
    { artifact_kind: "trace", logical_path: "native/b.jsonl", bytes: Buffer.from("{}\n") },
  ]);
  assert.equal(repeated.length, 1);

  assert.throws(
    () =>
      dedupeTraceObjects([
        { artifact_kind: "trace", logical_path: "native/a.jsonl", bytes: Buffer.from("one\n") },
        { artifact_kind: "trace", logical_path: "native/a.jsonl", bytes: Buffer.from("two\n") },
      ]),
    { code: "trace_manifest_conflict" },
  );
  assert.throws(
    () =>
      dedupeTraceObjects([
        { artifact_kind: "trace", logical_path: "../escape.jsonl", bytes: Buffer.from("x") },
      ]),
    { code: "trace_path_invalid" },
  );
});

test("manifest serialization omits bytes and unknown host paths", () => {
  const ctx = workspace();
  writeStdout(ctx.traceDir);
  writeFileSync(join(ctx.runtimeHome, ".claude", "projects", "-testbed", "s1.jsonl"), "{}\n");
  const bundle = collectTraceBundle({
    runtimeHome: ctx.runtimeHome,
    traceDir: ctx.traceDir,
    runtimeVersion: "2.1.278",
  });
  const reported = bundle.files.map((file, index) => ({
    ...file,
    object_key: `objects/${index}`,
  }));
  const manifest = traceManifestFromBundle(bundle, reported);
  assert.equal(manifest.capture_status, "complete");
  assert.ok(manifest.objects.every((item) => item.logical_path && !item.bytes));
  assert.equal(JSON.stringify(manifest).includes(ctx.runtimeHome), false);
});

test("writeDiagnostic creates a collectable diagnostic fallback", () => {
  const ctx = workspace();
  writeDiagnostic(ctx.traceDir, "execution.log", "failed before claude started\n");
  const bundle = collectTraceBundle({ runtimeHome: ctx.runtimeHome, traceDir: ctx.traceDir });
  const fallback = bundle.files.find((file) => file.logical_path === "diagnostics/execution.log");
  assert.ok(fallback);
  assert.equal(fallback.artifact_kind, "trace_diagnostic");
  assert.equal(bundle.capture_status, "partial");
});
