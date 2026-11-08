import { writeFileSync } from "node:fs";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * At-least-once callback delivery. Retries network errors, 429 and 5xx with a
 * bounded exponential backoff; never retries other 4xx. event_id is sent as
 * Idempotency-Key so the platform can dedupe retransmissions.
 */
export async function postCallback(
  url,
  payload,
  { eventId, retries = 4, baseDelayMs = 10, token, timeoutMs = 5000 } = {},
) {
  if (url.startsWith("file://")) {
    writeFileSync(new URL(url), `${JSON.stringify(payload, null, 2)}\n`);
    return { ok: true, status: 200, attempts: 1, body: "" };
  }

  let last = { ok: false, status: 0, attempts: 0, body: "" };
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(eventId ? { "idempotency-key": eventId } : {}),
          ...(token ? { authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(timeoutMs),
      });
      const body = await response.text();
      last = { ok: response.ok, status: response.status, attempts: attempt, body };
      if (response.ok) return last;
      if (response.status === 409) return { ...last, stale: true };
      if (response.status < 500 && response.status !== 429) return last;
    } catch (error) {
      last = { ok: false, status: 0, attempts: attempt, body: String(error.message ?? error) };
    }
    if (attempt < retries) await sleep(baseDelayMs * 2 ** (attempt - 1));
  }
  return last;
}
