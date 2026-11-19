import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runAgentExec } from "../../runner/lib/agent-exec.mjs";

test("SIGTERM cancels the Agent docker exec and returns the partial capture state", async () => {
  const bin = mkdtempSync(join(tmpdir(), "agent-exec-bin-"));
  const fakeDocker = join(bin, "docker");
  // `kill` exits immediately; every other invocation blocks so the signal
  // handler is the only way out.
  writeFileSync(
    fakeDocker,
    '#!/bin/sh\nif [ "$1" = "kill" ]; then exit 0; fi\nexec sleep 30\n',
  );
  chmodSync(fakeDocker, 0o755);
  const previousPath = process.env.PATH;
  process.env.PATH = `${bin}:${previousPath}`;
  try {
    const running = runAgentExec(["exec", "fake-agent", "true"], {
      agentName: "fake-agent",
      timeout: 30_000,
    });
    setTimeout(() => process.kill(process.pid, "SIGTERM"), 50);
    const result = await running;
    assert.equal(result.cancelled, true);
    assert.equal(result.signal, "SIGTERM");
  } finally {
    process.env.PATH = previousPath;
    rmSync(bin, { recursive: true, force: true });
  }
});
