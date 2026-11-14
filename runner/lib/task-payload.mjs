/**
 * Assemble per-case task views from the platform's shard tasks response.
 *
 * The top-level response owns run-wide frozen inputs (model, images, runtime,
 * the M5-01 asset snapshot and the M8-14 egress proxy + its short-lived
 * token); each case object only carries case data. This helper is the single
 * place that copies the run-wide snapshot, proxy and credential onto every
 * case so the executor cannot silently skip asset or proxy injection.
 */
export function buildCaseTask(task, taskPayload) {
  return {
    ...task,
    model: taskPayload?.model,
    execution_image_digest: taskPayload?.execution_image_digest,
    scoring_image_digest: taskPayload?.scoring_image_digest,
    environment_image: task.environment_image ?? taskPayload?.environment_image,
    adapter_key: taskPayload?.adapter_key || "swebench",
    runtime_key: taskPayload?.runtime?.key || "claude-code",
    asset_snapshot: taskPayload?.asset_snapshot ?? {},
    egress_proxy: taskPayload?.egress_proxy ?? task.egress_proxy ?? null,
    egress_token: taskPayload?.egress_token ?? task.egress_token ?? null,
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
