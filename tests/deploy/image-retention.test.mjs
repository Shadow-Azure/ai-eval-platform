import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

/**
 * M8-23: deployment pulls a new immutable api/web digest on every run, so the
 * host must bound its local image cache. These tests drive the real prune
 * helper in deploy/common.sh with a fake docker CLI and pin the keep-set
 * boundaries from the issue: running images, the .state/previous.env rollback
 * digest, and the newest N versions. Old versions must be removed, base images
 * must never be touched, and a failed removal must not fail the deployment.
 */

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const common = join(root, "deploy", "common.sh");
const deployScript = join(root, "deploy", "deploy.sh");
const read = (path) => readFileSync(path, "utf8");

const API_REPO = "registry.example.com/ns/ai-eval-platform-api";
const WEB_REPO = "registry.example.com/ns/ai-eval-platform-web";
const PROXY_REPO = "registry.example.com/ns/ai-eval-platform-proxy";

const image = (id, repo, created, digest = `<none>`, tag = `<none>`) => ({
  id,
  repo,
  tag,
  digest,
  created,
});

const baseImages = [
  image("b00000000001", "postgres", "2026-10-01 00:00:00 +0800 CST", `<none>`, "16-alpine"),
  image("b00000000002", "redis", "2026-10-01 00:00:00 +0800 CST", `<none>`, "7-alpine"),
];

function writeSandbox({ images, containers = [], previous = {}, locked = [] }) {
  const dir = mkdtempSync(join(tmpdir(), "image-retention-"));
  const state = join(dir, "state");
  const bin = join(dir, "bin");
  const host = join(dir, "host");
  mkdirSync(state);
  mkdirSync(bin);
  mkdirSync(join(host, ".state"), { recursive: true });
  // common.sh reads API_BIND_ADDRESS from the host .env at source time; the
  // file exists on the real host, so the fixture creates one too.
  writeFileSync(join(host, ".env"), "# managed by the deploy workflow\n");

  const lines = (items) => (items.length === 0 ? "" : `${items.join("\n")}\n`);
  writeFileSync(
    join(host, ".state", "previous.env"),
    Object.entries(previous)
      .map(([key, value]) => `${key}=${value}`)
      .join("\n") + "\n",
  );
  writeFileSync(
    join(state, "images.tsv"),
    lines(images.map((row) => [row.repo, row.tag, row.digest, row.id, row.created].join("|"))),
  );
  writeFileSync(join(state, "running"), lines(containers.map((container) => container.id)));
  writeFileSync(
    join(state, "containers"),
    lines(containers.map((container) => `${container.id}|sha256:${container.image.padEnd(64, "0")}`)),
  );
  writeFileSync(join(state, "locked"), lines(locked));
  writeFileSync(join(state, "removed"), "");

  const docker = `#!/usr/bin/env bash
set -u
state="\${FAKE_DOCKER_STATE:?}"
case "\${1:-}" in
  ps)
    cat "$state/running"
    ;;
  inspect)
    cid="\${4:-}"
    grep -F "$cid|" "$state/containers" | head -n 1 | cut -d'|' -f2
    ;;
  image)
    case "\${2:-}" in
      ls)
        cat "$state/images.tsv"
        ;;
      rm)
        shift 2
        rc=0
        for id in "$@"; do
          if grep -Fxq -- "$id" "$state/locked"; then
            echo "Error response from daemon: conflict: unable to delete $id (must be forced) - image is being used by stopped container" >&2
            rc=1
            continue
          fi
          if grep -q "|$id|" "$state/images.tsv"; then
            grep -v "|$id|" "$state/images.tsv" >"$state/images.tmp"
            mv "$state/images.tmp" "$state/images.tsv"
            printf '%s\\n' "$id" >>"$state/removed"
          else
            echo "Error: No such image: $id" >&2
            rc=1
          fi
        done
        exit "$rc"
        ;;
      *)
        exit 2
        ;;
    esac
    ;;
  *)
    exit 2
    ;;
esac
`;
  writeFileSync(join(bin, "docker"), docker);
  chmodSync(join(bin, "docker"), 0o755);
  return { dir, state, bin, host };
}

function runPrune(sandbox, retention) {
  const script = [
    "set -euo pipefail",
    'source "${COMMON_SH:?}"',
    'if prune_old_images; then echo "PRUNE_RESULT=ok"; else echo "PRUNE_RESULT=failed"; fi',
    'printf "RESULT kept=%s deleted=%s failed=%s\\n" "$PRUNE_KEPT" "$PRUNE_DELETED" "$PRUNE_FAILED"',
  ].join("\n");
  return spawnSync("bash", ["-c", script], {
    encoding: "utf8",
    env: {
      ...process.env,
      PATH: `${sandbox.bin}:${process.env.PATH}`,
      FAKE_DOCKER_STATE: sandbox.state,
      DEPLOY_DIR: sandbox.host,
      COMMON_SH: common,
      API_IMAGE: `${API_REPO}@sha256:api-running`,
      WEB_IMAGE: `${WEB_REPO}@sha256:web-running`,
      RUNNER_EGRESS_PROXY_IMAGE: `${PROXY_REPO}@sha256:proxy-running`,
      IMAGE_RETENTION_VERSIONS: retention ?? "2",
    },
  });
}

function counts(result) {
  assert.equal(result.status, 0, result.stderr);
  const match = result.stdout.match(/RESULT kept=(\d+) deleted=(\d+) failed=(\d+)/);
  assert.ok(match, `missing result line in:\n${result.stdout}\n${result.stderr}`);
  return { kept: Number(match[1]), deleted: Number(match[2]), failed: Number(match[3]) };
}

function removed(sandbox) {
  return readFileSync(join(sandbox.state, "removed"), "utf8").split("\n").filter(Boolean).sort();
}

function retentionFixture() {
  return {
    images: [
      ...baseImages,
      // Newest first in docker's list order; the helper sorts defensively, so
      // purposefully feed them out of order to exercise that sort.
      image("a00000000003", API_REPO, "2026-11-14 03:00:00 +0800 CST", "sha256:api-a3"),
      image("a00000000005", API_REPO, "2026-11-14 05:00:00 +0800 CST", "sha256:api-a5"),
      image("a00000000001", API_REPO, "2026-11-14 01:00:00 +0800 CST", "sha256:api-a1"),
      image("a00000000004", API_REPO, "2026-11-14 04:00:00 +0800 CST", "sha256:api-a4"),
      image("a00000000002", API_REPO, "2026-11-14 02:00:00 +0800 CST", "sha256:api-a2"),
      image("c00000000003", WEB_REPO, "2026-11-14 03:00:00 +0800 CST", "sha256:web-w3"),
      image("c00000000001", WEB_REPO, "2026-11-14 01:00:00 +0800 CST", "sha256:web-w1"),
      image("c00000000002", WEB_REPO, "2026-11-14 02:00:00 +0800 CST", "sha256:web-w2"),
      image("d00000000004", PROXY_REPO, "2026-11-14 04:00:00 +0800 CST", "sha256:proxy-p4"),
      image("d00000000002", PROXY_REPO, "2026-11-14 02:00:00 +0800 CST", "sha256:proxy-p2"),
      image("d00000000001", PROXY_REPO, "2026-11-14 01:00:00 +0800 CST", "sha256:proxy-p1"),
      image("d00000000003", PROXY_REPO, "2026-11-14 03:00:00 +0800 CST", "sha256:proxy-p3"),
    ],
    containers: [
      // The api runs the newest api image, the web a middle one, the proxy an
      // old one: running images are kept regardless of the version window.
      { id: "api-container", image: "a00000000005" },
      { id: "web-container", image: "c00000000002" },
      { id: "proxy-container", image: "d00000000001" },
    ],
    // The rollback target is older than the retained window and must survive.
    previous: {
      API_IMAGE: `${API_REPO}@sha256:api-a3`,
      WEB_IMAGE: `${WEB_REPO}@sha256:web-w1`,
    },
  };
}

test("prune keeps running images, the rollback digest and the newest N versions", () => {
  const sandbox = writeSandbox(retentionFixture());
  try {
    const result = runPrune(sandbox, "2");
    const { kept, deleted, failed } = counts(result);
    assert.deepEqual(removed(sandbox), ["a00000000001", "a00000000002", "d00000000002"]);
    assert.deepEqual({ kept, deleted, failed }, { kept: 9, deleted: 3, failed: 0 });
    assert.match(result.stdout, /retention=2/);
    assert.match(result.stdout, /df -h \//);
    assert.match(result.stdout, /removed a00000000001/);
    assert.equal(result.stdout.includes("\nPRUNE_RESULT=ok"), true);
  } finally {
    rmSync(sandbox.dir, { recursive: true, force: true });
  }
});

test("prune is idempotent: a second run finds nothing left to remove", () => {
  const sandbox = writeSandbox(retentionFixture());
  try {
    counts(runPrune(sandbox, "2"));
    const firstRun = removed(sandbox);
    const result = runPrune(sandbox, "2");
    const { deleted } = counts(result);
    assert.equal(deleted, 0);
    assert.deepEqual(removed(sandbox), firstRun);
    assert.equal(firstRun.length, 3);
  } finally {
    rmSync(sandbox.dir, { recursive: true, force: true });
  }
});

test("a failed image removal is logged but does not fail the prune or the deploy", () => {
  const sandbox = writeSandbox({ ...retentionFixture(), locked: ["d00000000002"] });
  try {
    const result = runPrune(sandbox, "2");
    const { kept, deleted, failed } = counts(result);
    assert.deepEqual(removed(sandbox), ["a00000000001", "a00000000002"]);
    assert.deepEqual({ kept, deleted, failed }, { kept: 9, deleted: 2, failed: 1 });
    assert.equal(result.stdout.includes("PRUNE_RESULT=ok"), true);
    assert.match(result.stderr, /could not remove d00000000002/);
  } finally {
    rmSync(sandbox.dir, { recursive: true, force: true });
  }
});

test("an invalid retention value falls back to three and a huge one is capped at ten", () => {
  const sandbox = writeSandbox(retentionFixture());
  try {
    assert.match(runPrune(sandbox, "many").stdout, /retention=3/);
    const capped = runPrune(sandbox, "999");
    assert.match(capped.stdout, /retention=10/);
    const { deleted } = counts(capped);
    assert.equal(deleted, 0);
  } finally {
    rmSync(sandbox.dir, { recursive: true, force: true });
  }
});

test("base images outside the platform repositories are never candidates", () => {
  const sandbox = writeSandbox(retentionFixture());
  try {
    counts(runPrune(sandbox, "2"));
    const allRemoved = removed(sandbox);
    assert.equal(allRemoved.some((id) => id.startsWith("b")), false);
  } finally {
    rmSync(sandbox.dir, { recursive: true, force: true });
  }
});

test("deploy.sh prunes after a healthy deploy and keeps cleanup failures non-fatal", () => {
  const script = read(deployScript);
  const health = script.indexOf("stage_finish success \"/readyz 通过\"");
  const prune = script.indexOf("stage_begin image_prune");
  const record = script.indexOf("record_deployment succeeded");
  assert.ok(health >= 0 && prune > health && record > prune, "prune must sit between health check and success record");
  assert.match(
    script.slice(prune, record),
    /if prune_old_images; then[\s\S]*stage_finish skipped/,
    "cleanup must run in a condition so a failure falls back to a skipped stage",
  );
  assert.ok(script.includes("stage_finish skipped \"镜像清理失败"));
});

test("common.sh bounds retention, reads the rollback state and removes by image id", () => {
  const helper = read(common);
  for (const expected of [
    'IMAGE_RETENTION_VERSIONS="${IMAGE_RETENTION_VERSIONS:-3}"',
    ".state/previous.env",
    "docker image ls --digests",
    "docker ps -q",
    "docker inspect --format",
    "docker image rm \"$remove_id\"",
    "df -h",
  ]) {
    assert.ok(helper.includes(expected), `common.sh missing ${expected}`);
  }
  assert.ok(helper.includes("limit=10"), "retention must be capped");
  assert.equal(helper.includes("docker system prune"), false, "a global prune could delete unrelated images");
  assert.equal(helper.includes("docker image prune"), false, "cleanup must target known platform image ids");
});
