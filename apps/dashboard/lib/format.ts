/**
 * lib/format.ts — shared display formatters for the dashboard.
 *
 * The one place a human-facing value is humanized so surfaces stay consistent and
 * never leak machine forms (raw ISO 8601, unseparated 7-digit counts, ragged money).
 * Every helper is fail-safe: nullish/empty → an em dash; an unparseable value is
 * returned verbatim rather than "Invalid Date" / "$NaN"; nothing throws.
 */

const EM_DASH = "—";

/**
 * Render an ISO 8601 timestamp as a short, locale-aware date+time
 * (e.g. "Jun 10, 2026, 12:05 PM"). Returns "—" for nullish/empty input and the
 * raw value unchanged when it can't be parsed — never throws, never blanks.
 */
export function formatTimestamp(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Render a count with locale thousands separators (e.g. 1234567 → "1,234,567").
 * Nullish or non-finite input → an em dash (never "NaN"). Small values are
 * unchanged, so surfaces asserting exact small counts stay byte-identical.
 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return value.toLocaleString("en-US");
}

/**
 * Render a USD amount as a currency string (e.g. "1.23" → "$1.23", 1234.5 →
 * "$1,234.50"). Accepts the gateway's NUMERIC-as-string cost fields or a number.
 * Nullish/empty → an em dash; a non-numeric string is returned verbatim rather
 * than "$NaN" — never throws.
 */
export function formatUsd(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return EM_DASH;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return typeof value === "string" ? value : EM_DASH;
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

/**
 * Humanize a spend-window bucket_start (a UTC date at 00:00) for a chart axis as a
 * short month + day ("Jun 10"). Always month+day — NOT the window granularity — so
 * that daily buckets inside a month window stay DISTINCT rather than all collapsing
 * to one "Jun 2026" tick. Rendered in UTC so a date-only bucket ("2026-06-01")
 * never slips a day in a negative-offset runner. Unparseable input is returned raw.
 */
export function formatBucketLabel(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return EM_DASH;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/**
 * Compact count for dense contexts like a chart axis (2529 → "2.5K", 561000 →
 * "561K"). Keeps ticks narrow so 4–7 digit values don't clip. Nullish/non-finite
 * → an em dash.
 */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EM_DASH;
  return value.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}
