/**
 * components/evals/format.ts — evals-console-local formatting helpers.
 *
 * The frozen wire shapes carry `created_at` as a UNIX-epoch NUMBER (unlike most of
 * this dashboard's ISO-8601 string timestamps), so the shared lib/format.ts
 * `formatTimestamp` (string in) needs a thin adapter rather than a new formatter.
 */

import { formatTimestamp } from "@/lib/format";
import type { EvalCaseAssertion } from "./types";

/** UNIX-epoch seconds -> the shared short locale date+time string ("—" on nullish). */
export function formatEpochSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return formatTimestamp(new Date(value * 1000).toISOString());
}

/**
 * The assertion's "expected" value as display text — M4's "assertion (kind + expected
 * value)". Prefers the conventional `expected` key (string verbatim; anything else
 * JSON-stringified); falls back to the rest of the assertion object (minus `kind`) for
 * assertion kinds that don't use `expected`, so no assertion payload is ever silently
 * dropped. Never throws — an unstringifiable value degrades to its String() form.
 */
export function formatExpected(assertion: EvalCaseAssertion): string {
  if (Object.prototype.hasOwnProperty.call(assertion, "expected")) {
    const expected = (assertion as Record<string, unknown>).expected;
    if (typeof expected === "string") return expected;
    if (expected === null || expected === undefined) return "—";
    try {
      return JSON.stringify(expected);
    } catch {
      return String(expected);
    }
  }
  const { kind: _kind, ...rest } = assertion;
  void _kind;
  if (Object.keys(rest).length === 0) return "—";
  try {
    return JSON.stringify(rest);
  } catch {
    return String(rest);
  }
}
