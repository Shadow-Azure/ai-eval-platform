/**
 * Dispatch descriptor fallbacks for the shard scripts.
 *
 * M8-51 (#323) option C: a minimal 1.1 dispatch carries only the fields a job
 * needs before it starts. The manifest digest and the dispatch expiry are
 * returned by the shard manifest response (`X-Manifest-Digest` /
 * `X-Manifest-Expires-At`), so a job reads them there when the legacy 1.0
 * fields are absent. Callers keep ownership of the actual comparison and error
 * code so each script reports the same diagnosable failure as before.
 */

export function dispatchManifestDigest(dispatch, manifestResponse) {
  return dispatch.manifest_digest || manifestResponse?.manifestDigest || null;
}

export function dispatchExpiresAt(dispatch, manifestResponse) {
  return dispatch.expires_at || manifestResponse?.expiresAt || null;
}

export function isExpired(isoTimestamp) {
  return Boolean(isoTimestamp) && Date.parse(isoTimestamp) < Date.now();
}
