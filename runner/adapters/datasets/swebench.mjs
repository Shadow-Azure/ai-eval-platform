// SWE-bench dataset adapter. The core runner knows nothing about /testbed,
// git patches, pytest or F2P/P2P; it only calls this adapter.
export const key = "swebench";

export function environmentImage(task) {
  return task.environment_image || task.scoring_image_digest || task.execution_image_digest;
}

export function applyTestPatchArgs(image, workdir, patchPath) {
  return [
    "run", "--rm", "-v", `${workdir}:/testbed`, "-v", `${patchPath}:/tmp/test.patch:ro`,
    image, "bash", "-lc", "cd /testbed && git apply --whitespace=nowarn /tmp/test.patch",
  ];
}

export function testArgs(image, workdir, testIds) {
  return [
    "run", "--rm", "-v", `${workdir}:/testbed`, image,
    "/opt/miniconda3/envs/testbed/bin/python", "-m", "pytest", "-q", "-rA",
    ...testIds,
  ];
}

export function testIds(task) {
  const expected = task.expected_metrics || {};
  return [...(expected.fail_to_pass || []), ...(expected.pass_to_pass || [])];
}

export function parseReport(task, output) {
  const expected = task.expected_metrics || {};
  const failToPass = expected.fail_to_pass || [];
  const passToPass = expected.pass_to_pass || [];
  const passed = new Set();
  const failed = new Set();
  for (const line of output.split("\n")) {
    const match = /^(PASSED|FAILED) (\S+)/.exec(line.trim());
    if (!match) continue;
    (match[1] === "PASSED" ? passed : failed).add(match[2]);
  }
  const success = (ids) => ids.filter((id) => passed.has(id) && !failed.has(id));
  const failure = (ids) => ids.filter((id) => !passed.has(id) || failed.has(id));
  const resolved =
    failToPass.length > 0 &&
    failToPass.every((id) => passed.has(id) && !failed.has(id)) &&
    passToPass.every((id) => passed.has(id) && !failed.has(id));
  return {
    score: {
      [task.case_key]: {
        resolved,
        tests_status: {
          FAIL_TO_PASS: { success: success(failToPass), failure: failure(failToPass) },
          PASS_TO_PASS: { success: success(passToPass), failure: failure(passToPass) },
          FAIL_TO_FAIL: { success: [], failure: [] },
          PASS_TO_FAIL: { success: [], failure: [] },
        },
      },
    },
    resultStatus: resolved ? "resolved" : "unresolved",
  };
}

export function workspacePath() {
  return "/testbed";
}

export function constraints() {
  return [
    "Do not modify test files; the official test patch is applied after you finish.",
    "Keep the change minimal and limited to production code.",
  ];
}
