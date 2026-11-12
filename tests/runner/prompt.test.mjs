import assert from "node:assert/strict";
import test from "node:test";

import { composePrompt } from "../../runner/lib/prompt.mjs";

const PROMPT = "Fix the separable model bug.\n";

test("empty suffixes keep the dataset prompt unchanged", () => {
  assert.equal(composePrompt(PROMPT, ""), PROMPT);
  assert.equal(composePrompt(PROMPT, undefined), PROMPT);
  assert.equal(composePrompt(PROMPT, null), PROMPT);
});

test("whitespace-only suffixes keep the dataset prompt unchanged", () => {
  assert.equal(composePrompt(PROMPT, "   "), PROMPT);
  assert.equal(composePrompt(PROMPT, "\n\t  "), PROMPT);
});

test("a suffix is appended under the additional instructions heading", () => {
  assert.equal(
    composePrompt(
      "Fix the separable model bug.",
      "This is a non-interactive evaluation. Use the superpowers skills and do not ask the user questions.",
    ),
    "Fix the separable model bug.\n\n## Additional instructions\nThis is a non-interactive evaluation. Use the superpowers skills and do not ask the user questions.",
  );
});

test("surrounding whitespace in the suffix is trimmed", () => {
  assert.equal(
    composePrompt("Fix the bug.", "\n  non-interactive evaluation  \n"),
    "Fix the bug.\n\n## Additional instructions\nnon-interactive evaluation",
  );
});
