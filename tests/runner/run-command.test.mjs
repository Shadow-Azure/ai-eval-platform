import assert from "node:assert/strict";
import test from "node:test";

import { runCommand } from "../../runner/lib/run-command.mjs";

test("captures stdout/stderr and preserves a non-zero exit code", async () => {
  const result = await runCommand(process.execPath, [
    "-e",
    "process.stdout.write('out'); process.stderr.write('err'); process.exitCode = 3;",
  ]);
  assert.equal(result.status, 3);
  assert.equal(result.stderr, "err");
  assert.equal(result.stdout, "out");
  assert.equal(result.error, null);
});

test("a missing executable resolves with the spawn error code", async () => {
  const result = await runCommand("/nonexistent/runner-command", [], { timeout: 1_000 });
  assert.equal(result.status, null);
  assert.equal(result.error?.code, "ENOENT");
});

test("timeout kills the child and maps ETIMEDOUT", async () => {
  const startedAt = Date.now();
  const result = await runCommand(process.execPath, ["-e", "setTimeout(() => {}, 5000)"], {
    timeout: 100,
  });
  assert.equal(result.status, null);
  assert.equal(result.error?.code, "ETIMEDOUT");
  assert.ok(Date.now() - startedAt < 3_000, "timeout should not wait for the child's full run");
});

test("AbortSignal kills the child and maps ABORT_ERR", async () => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 50);
  const result = await runCommand(process.execPath, ["-e", "setTimeout(() => {}, 5000)"], {
    signal: controller.signal,
  });
  clearTimeout(timer);
  assert.equal(result.status, null);
  assert.equal(result.error?.code, "ABORT_ERR");
});

test("an already-aborted signal resolves without spawning", async () => {
  let spawned = false;
  const result = await runCommand("node", [], {
    signal: AbortSignal.abort(),
    spawn: () => {
      spawned = true;
    },
  });
  assert.equal(spawned, false);
  assert.equal(result.error?.code, "ABORT_ERR");
});

test("maxBuffer truncates output and maps ENOBUFS", async () => {
  const result = await runCommand(
    process.execPath,
    ["-e", "process.stdout.write('x'.repeat(10000)); setTimeout(() => {}, 5000);"],
    { maxBuffer: 1000 },
  );
  assert.equal(result.status, null);
  assert.equal(result.error?.code, "ENOBUFS");
  assert.equal(result.stdout.length, 1000);
});

test("a long-running command keeps timers responsive", async () => {
  const order = [];
  const timer = new Promise((resolve) => {
    setTimeout(() => {
      order.push("timer");
      resolve();
    }, 50);
  });
  const command = runCommand(process.execPath, ["-e", "setTimeout(() => {}, 400)"], {
    timeout: 5_000,
  }).then((result) => {
    order.push("command");
    return result;
  });
  const [, result] = await Promise.all([timer, command]);
  assert.equal(result.status, 0);
  assert.deepEqual(order, ["timer", "command"]);
});

test("a supplied spawn implementation receives the command and stdio pipes", async () => {
  const calls = [];
  const result = await runCommand("docker", ["image", "inspect", "x"], {
    spawn: (command, args, options) => {
      calls.push({ command, args, options });
      return {
        stdout: { on() {} },
        stderr: { on() {} },
        once(event, handler) {
          if (event === "close") setImmediate(() => handler(0, null));
        },
        kill() {},
      };
    },
  });
  assert.equal(result.status, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, "docker");
  assert.deepEqual(calls[0].args, ["image", "inspect", "x"]);
  assert.deepEqual(calls[0].options.stdio, ["ignore", "pipe", "pipe"]);
});
