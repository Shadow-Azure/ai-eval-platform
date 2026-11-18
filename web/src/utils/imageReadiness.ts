import type { ImageReadiness } from "../api/evaluations";

/**
 * #290: the worker auto-requeues a failed image while its backoff
 * (`next_attempt_at`) is set, so the wizard must keep polling through the
 * visible failure instead of freezing the card. A failed row without a
 * `next_attempt_at` is terminal: stop polling until the operator retries,
 * rechecks, or ensures again.
 */
export function shouldPollImageReadiness(
  readiness: ImageReadiness | null | undefined,
): boolean {
  if (!readiness || readiness.ready) return false;
  if (readiness.preparing_count > 0 || readiness.missing_count > 0) return true;
  return readiness.images.some(
    (image) =>
      image.status === "failed" && (image.next_attempt_at ?? null) !== null,
  );
}
