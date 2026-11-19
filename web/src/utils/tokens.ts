/**
 * Token formatting shared by eval and dataset pages (M8-53 / issue #332).
 *
 * Token aggregates come from trace spans (`input_tokens + output_tokens`) and
 * follow the prototype's compact zh-CN scale: 万 / 亿. Cache read/write tokens
 * are a separate concept and must not be folded into these totals.
 */

/** Parse a number that may arrive as a JSON number or a decimal string. */
export function readTokenNumber(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** Format a token count compactly; missing/zero values render as an em dash. */
export function formatTokenAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) {
    return "—";
  }
  if (value >= 100_000_000) {
    return `${Number((value / 100_000_000).toFixed(2))} 亿`;
  }
  if (value >= 10_000) {
    return `${Number((value / 10_000).toFixed(1))} 万`;
  }
  return value.toLocaleString("zh-CN");
}
