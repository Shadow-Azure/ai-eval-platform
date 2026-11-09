import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  RuntimeError,
  buildClaudeCommand,
  consumeStreamLines,
  discoverTraceFiles,
  materializeAssets,
  runClaudeCode,
  validateAssets,
} from "../runner/lib/runtime-claude-code.mjs";

const assetFiles = {
  "skills/brainstorming/SKILL.md": "# brainstorming\n",
  "commands/plan.md": "# plan\n",
  "agents/reviewer.md": "# reviewer\n",
};

test("validates and materializes only the frozen asset snapshot", () => {
  assert.deepEqual(validateAssets(assetFiles), []);
  const workspace = mkdtempSync(join(tmpdir(), "claude-assets-"));
  const written = materializeAssets(assetFiles, workspace);
  assert.deepEqual(written, [
    ".claude/agents/reviewer.md",
    ".claude/commands/plan.md",
    ".claude/skills/brainstorming/SKILL.md",
  ]);
  assert.equal(
    readFileSync(join(workspace, ".claude/skills/brainstorming/SKILL.md"), "utf8"),
    "# brainstorming\n",
  );
});

test("rejects traversal and non-allowlisted asset paths", () => {
  assert.ok(validateAssets({ "../evil.md": "x" }).some((issue) => issue.code === "asset_path_invalid"));
  assert.ok(validateAssets({ "docs/readme.md": "x" }).some((issue) => issue.code === "asset_path_not_allowed"));
  assert.ok(validateAssets({ "skills/x/notes.md": "x" }).some((issue) => issue.code === "skill_manifest_missing"));
  assert.throws(() => materializeAssets({ "../evil": "x" }, tmpdir()), RuntimeError);
});

test("builds a structured command without a shell and strips provider keys", () => {
  const workspace = mkdtempSync(join(tmpdir(), "claude-command-"));
  const prompt = "fix it; rm -rf / # injected";
  process.env.ANTHROPIC_API_KEY = "provider-key";
  process.env.OPENAI_API_KEY = "provider-key";
  try {
    const spec = buildClaudeCommand({
      prompt,
      model: "MiniMax-M2.7-highspeed",
      workspace,
      runtimeHome: join(workspace, "home"),
      gatewayBaseUrl: "http://127.0.0.1:8000/model-gateway/anthropic/",
      gatewayToken: "v1.token.signature",
      executionImageDigest: "sha256:" + "a".repeat(64),
    });
    assert.equal(spec.command, "claude");
    assert.equal(spec.args[1], prompt);
    assert.ok(spec.args.includes("stream-json"));
    assert.ok(!spec.args.includes("--bare"));
    assert.ok(spec.args.includes("--dangerously-skip-permissions"));
    assert.equal(spec.cwd, workspace);
    assert.equal(spec.env.ANTHROPIC_AUTH_TOKEN, "v1.token.signature");
    assert.equal(spec.env.ANTHROPIC_BASE_URL, "http://127.0.0.1:8000/model-gateway/anthropic");
    assert.equal(spec.env.ANTHROPIC_API_KEY, undefined);
    assert.equal(spec.env.OPENAI_API_KEY, undefined);
    assert.equal(spec.executionImageDigest, "sha256:" + "a".repeat(64));
  } finally {
    delete process.env.ANTHROPIC_API_KEY;
    delete process.env.OPENAI_API_KEY;
  }
});

test("maps native stream JSONL to minimal live events", () => {
  const lines = [
    JSON.stringify({ type: "system", session_id: "s1", model: "m" }),
    JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "text", text: "inspect" }, { type: "tool_use", name: "Read" }] },
    }),
    JSON.stringify({ type: "stream_event", event: { type: "content_block_delta", delta: { text: "ok" } } }),
    JSON.stringify({ type: "result", subtype: "success", is_error: false, num_turns: 2 }),
    "not-json",
  ];
  const events = consumeStreamLines(lines);
  assert.deepEqual(
    events.map((event) => event.type),
    ["session_started", "assistant_message", "text_delta", "completed"],
  );
  assert.equal(events[0].session_id, "s1");
  assert.equal(events[1].tool_name, "Read");
});

test("runs a session and collects raw trace files", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "claude-run-"));
  const runtimeHome = join(workspace, "home");
  const bin = join(workspace, "claude");
  writeFileSync(
    bin,
    [
      "#!/usr/bin/env node",
      "const fs = require('node:fs');",
      "const path = require('node:path');",
      "console.log(JSON.stringify({type:'system',session_id:'s1',model:'MiniMax-M2.7-highspeed'}));",
      "console.log(JSON.stringify({type:'assistant',message:{content:[{type:'text',text:'working'}]}}));",
      "const dir = path.join(process.env.HOME, '.claude', 'projects', 'p');",
      "fs.mkdirSync(dir, {recursive:true});",
      "fs.writeFileSync(path.join(dir, 's1.jsonl'), '{\"type\":\"user\"}\\n');",
      "console.log(JSON.stringify({type:'result',subtype:'success',is_error:false,num_turns:1}));",
    ].join("\n"),
  );
  chmodSync(bin, 0o755);
  const result = await runClaudeCode({
    prompt: "fix",
    model: "MiniMax-M2.7-highspeed",
    workspace,
    runtimeHome,
    gatewayBaseUrl: "http://127.0.0.1:8000/model-gateway/anthropic",
    gatewayToken: "v1.token.signature",
    command: bin,
  });
  assert.equal(result.exitCode, 0);
  assert.equal(result.events.at(-1).type, "completed");
  assert.equal(result.traces.length, 1);
  assert.equal(result.traces[0].path, ".claude/projects/p/s1.jsonl");
  assert.equal(readFileSync(result.stdoutPath, "utf8").includes("session_started"), false);
  assert.ok(readFileSync(result.stdoutPath, "utf8").includes('"type":"system"'));
});

test("discovers only jsonl trace files", async () => {
  const runtimeHome = mkdtempSync(join(tmpdir(), "claude-traces-"));
  const { mkdirSync } = await import("node:fs");
  const dir = join(runtimeHome, ".claude/projects/p");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "s1.jsonl"), "line\n");
  writeFileSync(join(dir, "debug.log"), "ignored\n");
  const traces = await discoverTraceFiles(runtimeHome);
  assert.equal(traces.length, 1);
  assert.equal(traces[0].path, ".claude/projects/p/s1.jsonl");
});
