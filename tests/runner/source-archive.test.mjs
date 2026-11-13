import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  downloadSourceArchive,
  extractSourceArchive,
  injectSourceAssets,
  missingSourceAssets,
} from "../../runner/lib/source-archive.mjs";

const sha = (content) => `sha256:${createHash("sha256").update(content).digest("hex")}`;

function makeArchive(root, files, wrapper = "owner-repo-abc1234") {
  for (const [path, content] of Object.entries(files)) {
    const target = join(root, wrapper, path);
    mkdirSync(join(target, ".."), { recursive: true });
    writeFileSync(target, content);
  }
  const archive = join(root, "source.tar.gz");
  const result = spawnSync("tar", ["-czf", archive, "-C", root, wrapper], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const bytes = readFileSync(archive);
  return { bytes, digest: sha(bytes) };
}

test("downloadSourceArchive verifies the frozen digest", async () => {
  const dir = mkdtempSync(join(tmpdir(), "source-archive-test-"));
  try {
    const { bytes, digest } = makeArchive(dir, { "package.json": "{}" });
    const url = `file://${join(dir, "source.tar.gz")}`;
    const downloaded = await downloadSourceArchive({ source_archive_url: url, source_archive_digest: digest });
    assert.deepEqual(downloaded, bytes);
    await assert.rejects(
      downloadSourceArchive({
        source_archive_url: url,
        source_archive_digest: `sha256:${"0".repeat(64)}`,
      }),
      (error) => error.code === "source_archive_digest_mismatch",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("extractSourceArchive strips the wrapper and keeps nested files", () => {
  const dir = mkdtempSync(join(tmpdir(), "source-archive-test-"));
  try {
    const { bytes } = makeArchive(dir, {
      "package.json": "{}",
      "skills/s1/SKILL.md": "# s1\n",
    });
    const destination = join(dir, "checkout");
    extractSourceArchive(bytes, destination);
    assert.equal(readFileSync(join(destination, "package.json"), "utf8"), "{}");
    assert.equal(readFileSync(join(destination, "skills", "s1", "SKILL.md"), "utf8"), "# s1\n");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("extractSourceArchive rejects traversal entries", () => {
  const dir = mkdtempSync(join(tmpdir(), "source-archive-test-"));
  try {
    // A pre-built hostile archive (tar writer portability differs: GNU tar
    // strips "../" while bsdtar records it, so embed the bytes).
    const hostile = Buffer.from(
      "H4sIAIoS92oC/+3RMQrCQBAF0DlKTrBuIInnCZpekg14fIfYiL2C+F7zh9/8Yko5Ldtlvi2l3Vt8Rk3TMByZ3jOdX+7s+9qPU3Q1vmDf2rzmfPyn5/OvAQAAAAAAAAAAwG95AGz/SmMAKAAA",
      "base64",
    );
    const destination = join(dir, "checkout");
    assert.throws(
      () => extractSourceArchive(hostile, destination),
      (error) => error.code === "source_archive_invalid",
    );
    assert.equal(existsSync(join(dir, "checkout", "escape.txt")), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("injectSourceAssets copies static assets, skips generated ones, and verifies digests", () => {
  const dir = mkdtempSync(join(tmpdir(), "source-archive-test-"));
  try {
    const { bytes } = makeArchive(dir, {
      "skills/static-skill/SKILL.md": "# static\n",
      "commands/manual.md": "# manual\n",
    });
    const checkout = join(dir, "checkout");
    extractSourceArchive(bytes, checkout);
    const workspace = join(dir, "workspace");
    mkdirSync(workspace, { recursive: true });
    const snapshot = {
      assets: [
        {
          asset_type: "skill",
          name: "static-skill",
          path: "skills/static-skill",
          metadata: {
            generated: false,
            files: [{ path: "skills/static-skill/SKILL.md", sha256: sha("# static\n") }],
          },
        },
        {
          asset_type: "command",
          name: "manual",
          path: "commands/manual.md",
          metadata: {
            generated: false,
            runtime_path: "commands/manual.md",
            files: [{ path: "commands/manual.md", sha256: sha("# manual\n") }],
          },
        },
        {
          asset_type: "command",
          name: "opsx/propose",
          path: "generated/opsx/propose.md",
          metadata: { generated: true, runtime_path: "commands/opsx/propose.md", files: [] },
        },
      ],
    };
    assert.equal(injectSourceAssets(snapshot, checkout, workspace), 2);
    assert.equal(
      readFileSync(join(workspace, ".claude", "skills", "static-skill", "SKILL.md"), "utf8"),
      "# static\n",
    );
    assert.equal(
      readFileSync(join(workspace, ".claude", "commands", "manual.md"), "utf8"),
      "# manual\n",
    );
    assert.deepEqual(missingSourceAssets(snapshot, workspace), [
      ".claude/commands/opsx/propose.md",
    ]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
