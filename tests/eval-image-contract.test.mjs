import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dockerfile = readFileSync(new URL("../infra/eval-image/Dockerfile", import.meta.url), "utf8");
const buildScript = readFileSync(new URL("../infra/eval-image/build.sh", import.meta.url), "utf8");

test("evaluation image bundles Claude Code without provider secrets", () => {
  assert.match(dockerfile, /ARG NODE_IMAGE=node:22-bookworm-slim/);
  assert.match(dockerfile, /@anthropic-ai\/claude-code@\$\{CLAUDE_CODE_VERSION\}/);
  assert.match(dockerfile, /ARG CLAUDE_CODE_VERSION=2\.1\.\d+/);
  assert.match(dockerfile, /DEBIAN_MIRROR/);
  assert.match(dockerfile, /USER eval/);
  assert.doesNotMatch(dockerfile, /ANTHROPIC_API_KEY/);
  assert.doesNotMatch(dockerfile, /ANTHROPIC_AUTH_TOKEN/);
  assert.doesNotMatch(dockerfile, /sk-[A-Za-z0-9]/);
  assert.doesNotMatch(dockerfile, /--bare/);
});

test("build script pins the Claude Code version and prints the image digest", () => {
  assert.match(buildScript, /docker build/);
  assert.match(buildScript, /CLAUDE_CODE_VERSION/);
  assert.match(buildScript, /docker image inspect --format '\{\{\.Id\}\}'/);
  assert.match(buildScript, /digest=/);
});
