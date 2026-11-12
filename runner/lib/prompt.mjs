/**
 * Compose the prompt handed to the agent runtime.
 *
 * An evaluation may carry optional "additional instructions" (the frozen
 * `prompt_suffix`): they are appended verbatim under a stable heading after the
 * dataset problem statement. Empty or whitespace-only suffixes keep the
 * dataset prompt byte-for-byte, so runs without instructions behave exactly as
 * before.
 */
export function composePrompt(prompt, suffix) {
  const extra = typeof suffix === "string" ? suffix.trim() : "";
  if (!extra) return prompt;
  return `${prompt ?? ""}\n\n## Additional instructions\n${extra}`;
}
