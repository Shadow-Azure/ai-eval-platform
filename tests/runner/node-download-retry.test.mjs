import test from "node:test";
import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

import { getRuntimeAdapter } from "../../runner/lib/adapters.mjs";

// #397: the Node tarball download may be reset by the network (curl 56 etc.).
// Retry exactly once, but only for the confirmed network exit codes. Every
// deterministic curl failure and every other install step must keep failing
// immediately with the original exit code and stderr.
const NETWORK_CODES = [6, 7, 28, 35, 52, 55, 56];
const DETERMINISTIC_CODES = [22, 23, 60, 63, 77];

function writeExecutable(path, body) {
  writeFileSync(path, body, { mode: 0o755 });
  chmodSync(path, 0o755);
}

/**
 * Build a PATH with fake curl and fake install commands. The generated install
 * script performs no real filesystem work in this harness: every command it
 * invokes after the download succeeds is stubbed, and node is absent so the
 * download branch runs.
 */
function makeHarness({ firstCode = 0, secondCode = 0 } = {}) {
  const dir = mkdtempSync(join(tmpdir(), "node-download-retry-"));
  const bin = join(dir, "bin");
  mkdirSync(bin);
  const log = join(dir, "calls.log");
  const counter = join(dir, "curl.count");

  writeExecutable(
    join(bin, "curl"),
    `#!/bin/sh
n=0
[ -f "${counter}" ] && read -r n < "${counter}"
n=$((n + 1))
printf '%s\\n' "$n" > "${counter}"
printf 'curl %s\\n' "$*" >> "${log}"
if [ "$n" -eq 1 ] && [ "${firstCode}" -ne 0 ]; then
  echo "curl: (${firstCode}) simulated first failure" >&2
  exit ${firstCode}
fi
if [ "$n" -ge 2 ] && [ "${secondCode}" -ne 0 ]; then
  echo "curl: (${secondCode}) simulated second failure" >&2
  exit ${secondCode}
fi
out=""
prev=""
for arg in "$@"; do
  [ "$prev" = "-o" ] && out="$arg"
  prev="$arg"
done
# Keep the harness inside its temp dir instead of writing the production
# /tmp/node.tar.xz path, so parallel runs cannot race on one host file.
[ "$out" = "/tmp/node.tar.xz" ] && out="$HOME/node.tar.xz"
[ -n "$out" ] && printf 'node-tarball\\n' > "$out"
exit 0
`,
  );

  for (const command of ["mkdir", "tar", "npm", "useradd", "chown", "claude"]) {
    writeExecutable(
      join(bin, command),
      `#!/bin/sh\nprintf '${command} %s\\n' "$*" >> "${log}"\nexit 0\n`,
    );
  }

  return {
    dir,
    bin,
    log,
    download: join(dir, "node.tar.xz"),
    script: getRuntimeAdapter("claude-code").installScript(),
  };
}

function runInstall(harness) {
  return spawnSync("/bin/bash", ["-c", harness.script], {
    encoding: "utf8",
    env: { PATH: harness.bin, HOME: harness.dir },
    timeout: 10_000,
  });
}

function calls(harness, command) {
  let text = "";
  try {
    text = readFileSync(harness.log, "utf8");
  } catch {
    return 0;
  }
  return text.split("\n").filter((line) => line.startsWith(`${command} `)).length;
}

function withHarness(options, fn) {
  const harness = makeHarness(options);
  try {
    fn(harness);
  } finally {
    rmSync(harness.dir, { recursive: true, force: true });
  }
}

test("install script does not use curl's own retry flags", () => {
  const script = getRuntimeAdapter("claude-code").installScript();
  assert.doesNotMatch(script, /--retry/);
  assert.match(script, /node_url/);
});

test("does not retry when the first download succeeds", () => {
  withHarness({}, (harness) => {
    const result = runInstall(harness);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(calls(harness, "curl"), 1, "a successful download must not be repeated");
    assert.equal(calls(harness, "tar"), 1);
    assert.doesNotMatch(result.stderr, /retry 1\/1/);
    assert.equal(readFileSync(harness.download, "utf8"), "node-tarball\n");
    const curlLines = readFileSync(harness.log, "utf8")
      .split("\n")
      .filter((line) => line.startsWith("curl "));
    assert.equal(curlLines.length, 1);
    assert.match(curlLines[0], /node-v22\.14\.0-linux-x64\.tar\.xz/);
  });
});

test("retries the Node download exactly once for each confirmed network curl code", () => {
  for (const code of NETWORK_CODES) {
    withHarness({ firstCode: code }, (harness) => {
      const result = runInstall(harness);
      assert.equal(result.status, 0, `curl ${code} then success should recover: ${result.stderr}`);
      assert.equal(calls(harness, "curl"), 2, `curl ${code} should be retried once`);
      assert.match(result.stderr, new RegExp(`node download network error \\(curl ${code}\\), retry 1/1`));
      assert.equal(calls(harness, "tar"), 1, `curl ${code} retry should continue the install`);
      assert.equal(readFileSync(harness.download, "utf8"), "node-tarball\n");
      const curlLines = readFileSync(harness.log, "utf8")
        .split("\n")
        .filter((line) => line.startsWith("curl "));
      assert.equal(curlLines.length, 2);
      assert.ok(curlLines.every((line) => line.includes("node-v22.14.0-linux-x64.tar.xz")));
    });
  }
});

test("does not retry deterministic curl failures", () => {
  for (const code of DETERMINISTIC_CODES) {
    withHarness({ firstCode: code }, (harness) => {
      const result = runInstall(harness);
      assert.equal(result.status, code, `curl ${code} must keep its exit code`);
      assert.equal(calls(harness, "curl"), 1, `curl ${code} must not be retried`);
      assert.match(result.stderr, new RegExp(`curl: \\(${code}\\) simulated first failure`));
      assert.doesNotMatch(result.stderr, /retry 1\/1/);
      assert.equal(calls(harness, "tar"), 0);
      assert.equal(existsSync(harness.download), false);
    });
  }
});

test("a failed retry preserves the second attempt's exit code and stderr", () => {
  withHarness({ firstCode: 56, secondCode: 28 }, (harness) => {
    const result = runInstall(harness);
    assert.equal(result.status, 28, "the second curl failure must be the reported failure");
    assert.equal(calls(harness, "curl"), 2, "there is no third attempt");
    assert.match(result.stderr, /node download network error \(curl 56\), retry 1\/1/);
    assert.match(result.stderr, /curl: \(28\) simulated second failure/);
    assert.equal(calls(harness, "tar"), 0, "a failed download must not continue the install");
    assert.equal(existsSync(harness.download), false);
  });
});

test("a second 56 failure is still reported as 56", () => {
  withHarness({ firstCode: 56, secondCode: 56 }, (harness) => {
    const result = runInstall(harness);
    assert.equal(result.status, 56);
    assert.equal(calls(harness, "curl"), 2);
    assert.equal((result.stderr.match(/curl: \(56\)/g) || []).length, 2);
    assert.equal(calls(harness, "tar"), 0);
    assert.equal(existsSync(harness.download), false);
  });
});
