import { spawn, spawnSync } from "node:child_process";

/**
 * M8-43 (#304): the Agent docker exec must not block the event loop.
 *
 * The previous spawnSync call kept Node from handling SIGTERM for the whole
 * Agent run, so a GitHub job timeout killed the runner before it could upload a
 * partial trace or send a terminal callback. This variant uses spawn and adds
 * signal listeners only for the Agent phase; a cancellation kills the case
 * container (the Agent owner), returns the last stream fragments and lets the
 * caller upload the bind-mounted trace.
 */
export function runAgentExec(args, { agentName, timeout = 1_200_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let cancelled = false;
    let timer = null;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      process.removeListener("SIGTERM", onSignal);
      process.removeListener("SIGINT", onSignal);
      resolve(value);
    };
    const onSignal = (signal) => {
      if (cancelled) return;
      cancelled = true;
      // Killing the docker CLI does not stop the container. Kill the case
      // container so the Agent stops and the bind-mounted trace is flushed;
      // the caller's finally block removes it.
      try {
        child.kill("SIGTERM");
      } catch {
        // already gone
      }
      try {
        spawnSync("docker", ["kill", agentName], { encoding: "utf8", timeout: 2_000 });
      } catch {
        // The container may already have exited.
      }
      settle({ cancelled: true, signal, status: null, stdout, stderr });
    };
    process.on("SIGTERM", onSignal);
    process.on("SIGINT", onSignal);
    child.stdout.on("data", (chunk) => {
      if (stdout.length < 64 * 1024 * 1024) stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      if (stderr.length < 64 * 1024 * 1024) stderr += chunk;
    });
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      process.removeListener("SIGTERM", onSignal);
      process.removeListener("SIGINT", onSignal);
      reject(
        Object.assign(new Error(`docker 执行失败：${error.message}`), {
          code: "runtime_error",
        }),
      );
    });
    child.once("close", (status) => {
      if (settled) return;
      settle({ status, stdout, stderr });
    });
    if (timeout > 0) {
      timer = setTimeout(() => {
        try {
          child.kill("SIGTERM");
        } catch {
          // already gone
        }
        setTimeout(() => {
          try {
            child.kill("SIGKILL");
          } catch {
            // already gone
          }
        }, 5_000).unref?.();
      }, timeout);
    }
  });
}

