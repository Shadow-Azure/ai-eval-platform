/**
 * Assemble per-case task views from the platform's shard tasks response.
 *
 * The top-level response owns run-wide frozen inputs (model, images, runtime,
 * and the M5-01 asset snapshot); each case object only carries case data. This
 * helper is the single place that copies the run-wide snapshot onto every case
 * so the executor cannot silently skip asset injection.
 */
export function buildCaseTask(task, taskPayload) {
  return {
    ...task,
    model: taskPayload?.model,
    execution_image_digest: taskPayload?.execution_image_digest,
    scoring_image_digest: taskPayload?.scoring_image_digest,
    environment_image: taskPayload?.environment_image,
    adapter_key: taskPayload?.adapter_key || "swebench",
    runtime_key: taskPayload?.runtime?.key || "claude-code",
    asset_snapshot: taskPayload?.asset_snapshot ?? {},
  };
}

export function buildCaseTasks(taskPayload) {
  const byCase = new Map();
  for (const task of taskPayload?.cases ?? []) {
    if (!task || typeof task.case_run_id !== "string") continue;
    byCase.set(task.case_run_id, buildCaseTask(task, taskPayload));
  }
  return byCase;
}
