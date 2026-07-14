"use client";

/**
 * ScheduleControl — the REAL, server-side monthly Art. 12 bundle schedule toggle
 * (compliance-report-center TASK.md §3 CONTRACT — FROZEN @ v1, M19/M22/M23/R9/R10).
 *
 * REVISION: replaces the original draft's localStorage-only "reminder" fieldset
 * outright — that fieldset and its localStorage key ship NOWHERE in this build.
 * GET /admin/compliance/report-schedule succeeds for any AUDIT_READ role (read-only
 * for a non-owner); PUT is OWNER-only (Permission.SECURITY_CONFIG) — the Switch/day
 * picker is `disabled` for a non-owner with inline copy explaining why, but the
 * CURRENT server state still renders (M6: a non-owner can always see what's
 * configured, never a blank/hidden control).
 *
 * NOT ConfirmDialog-gated (unlike ZDR-enable in RetentionZdrSettings.tsx) —
 * toggling only starts/stops FUTURE ticks, never deletes an already-generated
 * report; a materially reversible action.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Switch,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  Loading,
  ErrorState,
} from "@/components/ui";
import { BffError } from "@/lib/bff-client";
import { getReportSchedule, putReportSchedule, type ReportSchedule } from "@/lib/compliance-reports";
import { formatTimestamp } from "@/lib/format";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

const SCHEDULE_QUERY_KEY = ["compliance-report-schedule"];
const DAYS_OF_MONTH = Array.from({ length: 28 }, (_, i) => i + 1);

const LAST_RUN_STATUS_LABEL: Record<string, string> = {
  success: "Generated successfully",
  skipped_zdr: "Skipped — Zero-Data-Retention is enabled",
  failed: "Failed — will retry automatically",
};

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function ScheduleControl() {
  const queryClient = useQueryClient();
  const { data: user } = useCurrentUser();
  const isOwner = user?.role === "owner";

  const { data, isLoading, isError, error } = useQuery<ReportSchedule>({
    queryKey: SCHEDULE_QUERY_KEY,
    queryFn: getReportSchedule,
    retry: false,
  });

  const [enabled, setEnabled] = useState(false);
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [mutError, setMutError] = useState<string | null>(null);

  const [seeded, setSeeded] = useState<ReportSchedule | undefined>(undefined);
  if (data && data !== seeded) {
    setSeeded(data);
    setEnabled(data.enabled);
    setDayOfMonth(data.dayOfMonth);
  }

  const save = useMutation({
    mutationFn: (body: { enabled: boolean; dayOfMonth?: number }) => putReportSchedule(body),
    onSuccess: (resp) => {
      setMutError(null);
      queryClient.setQueryData<ReportSchedule>(SCHEDULE_QUERY_KEY, resp);
    },
    onError: (err) => {
      // R10: a defensive backend reject (e.g. 403) reverts the displayed control to
      // the last-known-good server state — never leaves an unconfirmed value shown.
      const known = queryClient.getQueryData<ReportSchedule>(SCHEDULE_QUERY_KEY);
      setEnabled(known?.enabled ?? false);
      setDayOfMonth(known?.dayOfMonth ?? 1);
      setMutError(getErrorTitle(err));
    },
  });

  function handleToggle(next: boolean) {
    setEnabled(next);
    setMutError(null);
    save.mutate({ enabled: next, dayOfMonth });
  }

  function handleDayChange(next: string) {
    const parsed = Number(next);
    setDayOfMonth(parsed);
    setMutError(null);
    if (enabled) {
      save.mutate({ enabled: true, dayOfMonth: parsed });
    }
  }

  if (isLoading) {
    return <Loading label="Loading scheduled generation" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  const controlsDisabled = !isOwner || save.isPending;

  return (
    <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <legend className="px-1 text-sm font-semibold text-foreground">Scheduled generation</legend>
      <p className="text-xs text-muted-foreground">
        When enabled, a new Art. 12 bundle is generated automatically and unattended
        once a month — this is REAL server-side scheduling, not a reminder. Each
        bundle is stored server-side and appears in &quot;Generated reports&quot;
        below. Generation fires at UTC midnight on the selected day of month.
      </p>

      <div className="flex items-center justify-between gap-4">
        <label htmlFor="schedule-enabled" className="text-sm font-medium text-foreground">
          Enable scheduled generation
        </label>
        <Switch
          id="schedule-enabled"
          aria-label="Enable scheduled generation"
          checked={enabled}
          disabled={controlsDisabled}
          onCheckedChange={handleToggle}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="schedule-day-of-month" className="text-sm font-medium text-foreground">
          Day of month
        </label>
        <Select
          value={String(dayOfMonth)}
          onValueChange={handleDayChange}
          disabled={controlsDisabled}
        >
          <SelectTrigger id="schedule-day-of-month" aria-label="Day of month">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DAYS_OF_MONTH.map((d) => (
              <SelectItem key={d} value={String(d)}>
                {d}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {!isOwner && (
        <p className="text-xs text-muted-foreground">Only the tenant owner can change this.</p>
      )}

      {data?.lastRunAt && (
        <p className="text-xs text-muted-foreground">
          Last run: {formatTimestamp(data.lastRunAt)} —{" "}
          {data.lastRunStatus ? (LAST_RUN_STATUS_LABEL[data.lastRunStatus] ?? data.lastRunStatus) : "—"}
          .
        </p>
      )}

      {mutError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {mutError}
        </p>
      )}
    </fieldset>
  );
}
