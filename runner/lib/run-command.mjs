/**
 * Promise wrapper around `child_process.spawn` for runner commands that can
 * take seconds.
 *
 * #363: image tarball download + `docker load` and `git checkout` used to run
 * through `spawnSync`. On a cold spot runner the tarball path takes tens of
 * seconds, and a synchronous child keeps the Node event loop from draining
 * socket-close events. The per-case manifest refresh that follows then reused
 * a keep-alive connection the platform had already closed (#362). Awaiting a
 * spawned child keeps the loop responsive while preserving the timeout,
 * output-capture and exit-code behaviour callers already branch on.
 *
 * Command failures resolve (like `spawnSync`) instead of rejecting: callers
 * already inspect `status`/`error` and write their own stage diagnostics.
 */
import { spawn as nodeSpawn } from "node:child_process";

const DEFAULT_MAX_BUFFER = 1024 * 1024;
const KILL_GRACE_MS = 5_000;

function commandError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function killChild(child, signal = "SIGTERM") {
  try {
    child.kill(signal);
  } catch {
    // The child may have exited between the timer firing and the kill.
  }
}

/**
 * Run a command and resolve with its output and exit status.
 *
 * @param {string} command
 * @param {string[]} [args]
 * @param {{
 *   spawn?: Function,
 *   timeout?: number,
 *   maxBuffer?: number,
 *   signal?: AbortSignal,
 *   env?: NodeJS.ProcessEnv,
 *   cwd?: string,
 * }} [options]
 * @returns {Promise<{status: number|null, signal: string|null, stdout: string, stderr: string, error: Error|null}>}
 */
export function runCommand(command, args = [], options = {}) {
  const spawn = options.spawn ?? nodeSpawn;
  const timeout = Number.isFinite(options.timeout) && options.timeout > 0 ? options.timeout : 0;
  const maxBuffer =
    Number.isFinite(options.maxBuffer) && options.maxBuffer >= 0
      ? options.maxBuffer
      : DEFAULT_MAX_BUFFER;
  const abortSignal = options.signal ?? null;

  return new Promise((resolve) => {
    const failBeforeSpawn = (code, message) => {
      resolve({
        status: null,
        signal: null,
        stdout: "",
        stderr: "",
        error: commandError(code, message),
      });
    };

    if (abortSignal?.aborted) {
      failBeforeSpawn("ABORT_ERR", `命令已取消：${command}`);
      return;
    }

    let child;
    try {
      child = spawn(command, args, {
        env: options.env,
        cwd: options.cwd,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      resolve({ status: null, signal: null, stdout: "", stderr: "", error });
      return;
    }

    const stdoutChunks = [];
    const stderrChunks = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let failure = null;
    let settled = false;
    let timeoutTimer = null;
    let killTimer = null;

    const cleanup = () => {
      if (timeoutTimer) clearTimeout(timeoutTimer);
      if (killTimer) clearTimeout(killTimer);
      abortSignal?.removeEventListener?.("abort", onAbort);
    };

    const finish = (status, signal) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve({
        // A killed child reports no status, matching spawnSync's result shape.
        status: failure ? null : (status ?? null),
        signal: signal ?? null,
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
        error: failure,
      });
    };

    const stop = (code, message) => {
      if (settled || failure) return;
      failure = commandError(code, message);
      killChild(child);
      // SIGTERM is usually enough; escalate if a child ignores it so a stuck
      // command cannot hang the runner forever.
      killTimer = setTimeout(() => killChild(child, "SIGKILL"), KILL_GRACE_MS);
      killTimer.unref?.();
    };

    const onAbort = () => stop("ABORT_ERR", `命令已取消：${command}`);

    const capture = (stream, chunks, streamName) => {
      if (!stream) return;
      stream.on("data", (chunk) => {
        if (settled || failure) return;
        const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
        const used = streamName === "stdout" ? stdoutBytes : stderrBytes;
        const remaining = maxBuffer - used;
        if (buffer.length > remaining) {
          if (remaining > 0) chunks.push(buffer.subarray(0, remaining));
          if (streamName === "stdout") stdoutBytes = maxBuffer;
          else stderrBytes = maxBuffer;
          stop("ENOBUFS", `命令输出超过 maxBuffer（${maxBuffer} 字节）：${command}`);
          return;
        }
        chunks.push(buffer);
        if (streamName === "stdout") stdoutBytes += buffer.length;
        else stderrBytes += buffer.length;
      });
      stream.on("error", (error) => stop(error?.code || "EIO", error?.message || "输出流读取失败"));
    };

    capture(child.stdout, stdoutChunks, "stdout");
    capture(child.stderr, stderrChunks, "stderr");

    child.once("error", (error) => {
      if (settled) return;
      // Keep the spawn error's code (ENOENT, EACCES, ...) for diagnostics.
      failure = failure ?? error;
      finish(null, null);
    });
    child.once("close", (status, signal) => finish(status, signal));

    if (abortSignal) abortSignal.addEventListener("abort", onAbort, { once: true });
    if (timeout) {
      timeoutTimer = setTimeout(
        () => stop("ETIMEDOUT", `命令超时（${timeout}ms）：${command}`),
        timeout,
      );
    }
  });
}
