/**
 * lib/compliance-reports.ts — pure bffGet/bffPut wrappers over the schedule +
 * generated-reports-list endpoints (compliance-report-center TASK.md §3 CONTRACT —
 * FROZEN @ v1, M18/M19). No React/DOM dependency; field names mapped snake_case
 * (wire) <-> camelCase (TS), mirroring every other `lib/*.ts` wrapper's own
 * convention in this codebase (no new pattern introduced).
 */

import { bffGet, bffPut } from "./bff-client";

export interface ReportSchedule {
  enabled: boolean;
  cadence: "monthly";
  dayOfMonth: number;
  windowPolicy: "previous_calendar_month";
  createdBy: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  lastRunAt: string | null;
  lastRunStatus: "success" | "skipped_zdr" | "failed" | null;
  nextRunAt: string | null;
}

export interface GeneratedReportSummary {
  id: string;
  periodStart: string;
  periodEnd: string;
  generatedAt: string;
  sizeBytes: number;
  formatVersion: string;
}

interface ReportScheduleWire {
  enabled: boolean;
  cadence: "monthly";
  day_of_month: number;
  window_policy: "previous_calendar_month";
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_run_at: string | null;
  last_run_status: "success" | "skipped_zdr" | "failed" | null;
  next_run_at: string | null;
}

interface GeneratedReportItemWire {
  id: string;
  period_start: string;
  period_end: string;
  generated_at: string;
  size_bytes: number;
  format_version: string;
}

interface GeneratedReportsListWire {
  items: GeneratedReportItemWire[];
  next_cursor: string | null;
  has_more: boolean;
}

function fromWireSchedule(wire: ReportScheduleWire): ReportSchedule {
  return {
    enabled: wire.enabled,
    cadence: wire.cadence,
    dayOfMonth: wire.day_of_month,
    windowPolicy: wire.window_policy,
    createdBy: wire.created_by,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    lastRunAt: wire.last_run_at,
    lastRunStatus: wire.last_run_status,
    nextRunAt: wire.next_run_at,
  };
}

function fromWireReport(wire: GeneratedReportItemWire): GeneratedReportSummary {
  return {
    id: wire.id,
    periodStart: wire.period_start,
    periodEnd: wire.period_end,
    generatedAt: wire.generated_at,
    sizeBytes: wire.size_bytes,
    formatVersion: wire.format_version,
  };
}

/** GET /admin/compliance/report-schedule — any AUDIT_READ role. */
export async function getReportSchedule(): Promise<ReportSchedule> {
  const wire = await bffGet<ReportScheduleWire>("/admin/compliance/report-schedule");
  return fromWireSchedule(wire);
}

/** PUT /admin/compliance/report-schedule — SECURITY_CONFIG (OWNER only). */
export async function putReportSchedule(body: {
  enabled: boolean;
  dayOfMonth?: number;
}): Promise<ReportSchedule> {
  const wire = await bffPut<ReportScheduleWire>("/admin/compliance/report-schedule", {
    enabled: body.enabled,
    ...(body.dayOfMonth !== undefined ? { day_of_month: body.dayOfMonth } : {}),
  });
  return fromWireSchedule(wire);
}

/** GET /admin/compliance/reports — keyset list, generated_at DESC (M18). */
export async function listGeneratedReports(cursor?: string): Promise<{
  items: GeneratedReportSummary[];
  nextCursor: string | null;
  hasMore: boolean;
}> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const wire = await bffGet<GeneratedReportsListWire>(`/admin/compliance/reports${query}`);
  return {
    items: wire.items.map(fromWireReport),
    nextCursor: wire.next_cursor,
    hasMore: wire.has_more,
  };
}
