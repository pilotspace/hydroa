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
 * Humanize a spend-window bucket_start (always a UTC date at 00:00 — the gateway
 * truncates per window: day/week → a date, month → the month). Rendered in UTC so
 * a date-only bucket ("2026-06-01") never slips a day in a negative-offset runner.
 * Unparseable input is returned verbatim.
 */
export function formatBucketLabel(
  value: string | null | undefined,
  window?: "day" | "week" | "month",
): string {
  if (value === null || value === undefined || value === "") return EM_DASH;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const opts: Intl.DateTimeFormatOptions =
    window === "month"
      ? { month: "short", year: "numeric", timeZone: "UTC" }
      : { month: "short", day: "numeric", timeZone: "UTC" };
  return date.toLocaleDateString("en-US", opts);
}
