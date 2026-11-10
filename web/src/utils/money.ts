/**
 * Money formatting for the platform billing currency (M5-10).
 *
 * Every fee the console renders is CNY: resource spot prices/thresholds,
 * model token prices and usage cost, evaluation budgets and cost estimates.
 * Pages call these helpers instead of prefixing `$` or formatting decimals
 * by hand, so the unit label and precision stay consistent.
 */

export const BILLING_CURRENCY = "CNY";

const standard = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: BILLING_CURRENCY,
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const precise = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: BILLING_CURRENCY,
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

/** Format one amount; unit prices keep up to four decimals. */
export function formatMoney(
  value: string | number | null | undefined,
  options: { precise?: boolean } = {},
): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return (options.precise ? precise : standard).format(parsed);
}

/** Format one hourly unit price (spot quote or threshold). */
export function formatUnitPrice(value: string | number | null | undefined): string {
  return formatMoney(value, { precise: true });
}

/** Format a low–high estimate range. */
export function formatMoneyRange(
  low: string | number | null | undefined,
  high: string | number | null | undefined,
): string {
  if (low === null || low === undefined || high === null || high === undefined) {
    return "—";
  }
  return `${formatUnitPrice(low)} – ${formatUnitPrice(high)}`;
}
