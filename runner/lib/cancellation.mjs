// M8-43 (#304): terminal failure payload for a cancelled/timed-out job.
//
// The runner owns the shape; keeping it here lets the contract test assert the
// exact payload the SIGTERM handler sends without spawning a real Agent.
export function buildCancellationFailure({
  caseRunId,
  attempt,
  eventId,
  sequence,
  errorCode = "runner_lost",
  summary,
  phase = "executing",
  traceManifest = null,
}) {
  return {
    schema_version: "1.0",
    event_id: eventId,
    case_run_id: caseRunId,
    attempt,
    sequence,
    error_code: errorCode,
    // A job cancellation/timeout has already spent the case budget: a full
    // retry would repeat the same kill. The platform still honours this flag
    // even though generic runner_lost delivery is retryable.
    retryable: false,
    error_summary: String(summary || "job cancelled").slice(0, 1000),
    phase,
    ...(traceManifest && Array.isArray(traceManifest.objects) && traceManifest.objects.length > 0
      ? { trace_manifest: traceManifest }
      : {}),
  };
}
