import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";

const PREPARE = "infra/runner-image/prepare.sh";
const BUILD = "infra/runner-image/build.sh";
const CLOUD_INIT = "backend/app/services/provisioning/spot_cloud_init.py";

function read(path) {
  return readFileSync(path, "utf8");
}

test("runner image scripts are valid bash", () => {
  execFileSync("bash", ["-n", PREPARE]);
  execFileSync("bash", ["-n", BUILD]);
});

test("prepare.sh bakes dependencies and disables runtime auto-updates", () => {
  const script = read(PREPARE);
  assert.match(script, /docker\.io/);
  assert.match(script, /NODE_VERSION="\$\{NODE_VERSION:-22\.11\.0\}"/);
  assert.match(script, /apt-get install -y --no-install-recommends[\s\S]*auditd/);
  assert.match(script, /APT::Periodic::Update-Package-Lists "0";/);
  assert.match(script, /APT::Periodic::Unattended-Upgrade "0";/);
  assert.match(script, /systemctl mask apt-daily\.timer/);
  assert.match(script, /\$nrconf\{restart\} = 'l';/);
  assert.match(script, /IMAGE_MARKER="\/etc\/eval\/runner-image-baked"/);
  assert.match(script, /IMAGE_RECORD="\/etc\/eval\/runner-image\.json"/);
  assert.match(script, /"baked": true/);
  assert.match(script, /cloud-init clean --logs --seed/);
  // A reboot-required builder must not be snapshotted; build.sh reboots and
  // reruns prepare instead.
  assert.match(script, /exit 2/);
});

test("build.sh dry-run explains the prepare, verification and snapshot steps", () => {
  const output = execFileSync(
    "bash",
    [BUILD, "--instance-id", "i-test", "--version", "2026-11-21", "--dry-run"],
    { encoding: "utf8" },
  );
  assert.match(output, /RunCommand with base64/);
  assert.match(output, /RebootInstance/);
  assert.match(output, /verify \/var\/run\/reboot-required/);
  assert.match(output, /keeping builder i-test/);
  assert.match(output, /image ai-eval-runner-2026-11-21/);
});

test("cloud-init and the image builder share the same hardening contract", () => {
  const cloudInit = read(CLOUD_INIT);
  const prepare = read(PREPARE);
  for (const directive of [
    "APT::Periodic::Update-Package-Lists \"0\";",
    "APT::Periodic::Unattended-Upgrade \"0\";",
    "systemctl mask apt-daily.timer",
    "/etc/eval/runner-image-baked",
    "/var/run/reboot-required",
  ]) {
    assert.ok(cloudInit.includes(directive), `cloud-init missing ${directive}`);
    assert.ok(prepare.includes(directive), `prepare.sh missing ${directive}`);
  }
  // cloud-init is a Python f-string, so the rendered `$nrconf{restart}` is
  // escaped as `$nrconf{{restart}}` in the source.
  assert.ok(cloudInit.includes("$nrconf{{restart}} = 'l';"));
  assert.ok(prepare.includes("$nrconf{restart} = 'l';"));
});
