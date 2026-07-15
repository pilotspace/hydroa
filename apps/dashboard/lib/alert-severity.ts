/**
 * alert-severity.ts — client-side severity classification for alert events
 * (audit-remediation item 7).
 *
 * The backend alert_events schema carries NO `severity` field — confirmed against
 * gateway/usage/application/alert_writer.py, gateway/proxy/infrastructure/circuit_breaker.py,
 * and gateway/usage/application/drift_checker.py, which only ever emit an `event_type`
 * string. This module is a pure, presentation-layer mapping from the known event_type
 * values to a triage severity, used only for the Alerts page's filter + badge. It must
 * NEVER throw and must degrade an unrecognized event_type to "info" rather than fabricate
 * false urgency — an honest "we don't know" default, not a guess dressed up as fact.
 */

export type AlertSeverity = "critical" | "warning" | "info";

/** Upstream/circuit-breaker outages — actively broken right now. */
const CRITICAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  "circuit_breaker_open",
  "upstream_health_fail",
]);

/** Budget/drift conditions — needs attention, nothing is actively down. */
const WARNING_EVENT_TYPES: ReadonlySet<string> = new Set([
  "soft_budget_exceeded",
  "reconciliation_drift",
]);

/** Recovery/informational events — no action needed. Everything else not
 * listed above also degrades to "info" (see file header). */
export function classifyAlertSeverity(eventType: string): AlertSeverity {
  if (CRITICAL_EVENT_TYPES.has(eventType)) return "critical";
  if (WARNING_EVENT_TYPES.has(eventType)) return "warning";
  return "info";
}

export const ALERT_SEVERITIES: readonly AlertSeverity[] = ["critical", "warning", "info"];

export const SEVERITY_LABELS: Record<AlertSeverity, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
};

/** Maps to the shared Badge component's existing variant set (no new variant added). */
export const SEVERITY_BADGE_VARIANT: Record<AlertSeverity, "destructive" | "warning" | "secondary"> = {
  critical: "destructive",
  warning: "warning",
  info: "secondary",
};
