/**
 * lib/format.ts — shared display formatters for the dashboard.
 *
 * One humanizer for timestamps so the admin read tables (alerts / audit / usage)
 * never render raw ISO 8601 to a human. Locale-aware, fail-safe: null/empty → an
 * em dash; an unparseable value is returned verbatim rather than "Invalid Date".
 */

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
