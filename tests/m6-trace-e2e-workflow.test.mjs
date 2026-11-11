import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const workflow = readFileSync(".github/workflows/eval-shard.yml", "utf8");

test("trace E2E is explicit workflow_dispatch only", () => {
  assert.match(workflow, /trace-e2e:/);
  assert.match(
    workflow,
    /github\.event_name == 'workflow_dispatch' && inputs\.mode == 'trace-e2e'/,
  );
  assert.match(workflow, /runs-on: \[self-hosted, linux, eval\]/);
});

test("trace E2E uses real OSS and a disposable PostgreSQL database", () => {
  assert.match(workflow, /S3_ENDPOINT: \$\{\{ secrets\.OSS_ENDPOINT \}\}/);
  assert.match(workflow, /S3_ACCESS_KEY: \$\{\{ secrets\.S3_ACCESS_KEY \}\}/);
  assert.match(workflow, /TRACE_E2E_DATABASE_URL: postgresql\+psycopg:\/\//);
  assert.match(workflow, /--object-store oss/);
});

test("trace E2E installs the pinned real Claude Code runtime and uploads evidence", () => {
  assert.match(workflow, /@anthropic-ai\/claude-code@2\.1\.278/);
  assert.match(workflow, /tests\/integration\/m6_06_trace_e2e\.py/);
  assert.match(workflow, /actions\/upload-artifact@v4/);
  assert.match(workflow, /reports\/2026-11-12\/m6-06-trace-e2e-validation/);
});
