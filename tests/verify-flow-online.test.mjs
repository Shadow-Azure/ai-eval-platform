import assert from "node:assert/strict";
import test from "node:test";
import {
  checkIssueMilestone,
  checkIssueRemote,
  checkMilestoneRemote,
  checkPrRemote,
} from "../scripts/verify-flow-online.mjs";

test("maps local done status to a closed GitHub issue", () => {
  assert.deepEqual(checkIssueRemote("issue.md", "done", { state: "CLOSED" }), []);
  assert.match(
    checkIssueRemote("issue.md", "done", { state: "OPEN" }).join("\n"),
    /CLOSED/,
  );
});

test("requires the GitHub issue to belong to the expected milestone", () => {
  assert.deepEqual(
    checkIssueMilestone("issue.md", "M1", { milestone: { title: "M1" } }),
    [],
  );
  assert.match(
    checkIssueMilestone("issue.md", "M1", { milestone: null }).join("\n"),
    /未归属/,
  );
});

test("maps milestone done status to a closed GitHub milestone", () => {
  assert.deepEqual(
    checkMilestoneRemote(
      "milestone.md",
      { title: "M1", status: "done" },
      { title: "M1", state: "closed" },
    ),
    [],
  );
});

test("requires every pull request to link both a milestone and a project", () => {
  assert.deepEqual(
    checkPrRemote({ milestone: { title: "M1" }, projectItems: [{}] }),
    [],
  );
  assert.match(
    checkPrRemote({ milestone: null, projectItems: [] }).join("\n"),
    /milestone/,
  );
  assert.match(
    checkPrRemote({ milestone: { title: "M1" }, projectItems: [] }).join("\n"),
    /project/,
  );
});
