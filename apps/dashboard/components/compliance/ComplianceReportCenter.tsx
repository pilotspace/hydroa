"use client";

/**
 * ComplianceReportCenter — the Compliance tab's content (compliance-report-center
 * TASK.md §3 CONTRACT — FROZEN @ v1). Two halves:
 *   1. On-demand generation: a period picker walks the FROZEN
 *      GET /admin/compliance/art12-bundle route to completion (lib/art12-bundle.ts,
 *      UNCHANGED by this revision) and previews/downloads the assembled bundle.
 *   2. REAL scheduling + delivery: <ScheduleControl /> (M19/M22/M23) and
 *      <GeneratedReportsList /> (M18) for the unattended monthly path.
 *
 * Safety rule (frozen, §5 BUILD): the on-demand walk NEVER retries a failed page and
 * NEVER re-derives since/until mid-walk — one captured period, one pass, fail
 * closed on ANY non-2xx (no "best-effort partial download" fallback).
 *
 * Error handling: a 403 (ERR_AUTH_FORBIDDEN) is fundamentally un-retryable from this
 * control (the role simply lacks AUDIT_READ) — the WHOLE surface is replaced by an
 * ErrorState, no form. Every other error kind (401/422 payload/422 cursor/504/local
 * page-ceiling) keeps the picker mounted (session/timeout/period issues are
 * user-actionable) and renders a distinct ErrorState below it; the 422 cursor case
 * additionally relabels the button "Generate again" — clicking it starts a
 * completely fresh, un-tokened walk (assembleArt12Bundle always starts untokened).
 */

import { useState } from "react";
import { Button, Input, ErrorState } from "@/components/ui";
import { bffGet, BffError } from "@/lib/bff-client";
import {
  assembleArt12Bundle,
  BundleTooLargeError,
  type Art12AssembledBundle,
  type Art12BundleResponse,
} from "@/lib/art12-bundle";
import { formatTimestamp, formatNumber } from "@/lib/format";
import { BundleEvidenceSeal } from "./BundleEvidenceSeal";
import { ScheduleControl } from "./ScheduleControl";
import { GeneratedReportsList } from "./GeneratedReportsList";

type WalkState = "idle" | "generating" | "done" | "error";
type ErrorKind = "auth" | "forbidden" | "payload" | "cursor" | "timeout" | "too-large" | "unknown";

function classifyError(err: unknown): ErrorKind {
  if (err instanceof BundleTooLargeError) return "too-large";
  if (err instanceof BffError) {
    const code = err.problem.code;
    if (code === "ERR_AUTH_INVALID_TOKEN" || err.status === 401) return "auth";
    if (code === "ERR_AUTH_FORBIDDEN" || err.status === 403) return "forbidden";
    if (code === "ERR_CURSOR_INVALID") return "cursor";
    if (code === "ERR_PAYLOAD_INVALID" || err.status === 422) return "payload";
    if (code === "ERR_EXPORT_TIMEOUT" || err.status === 504) return "timeout";
  }
  return "unknown";
}

const ERROR_COPY: Record<ErrorKind, { title: string; description?: string }> = {
  auth: {
    title: "Your session has expired",
    description: "Sign in again, then generate the bundle.",
  },
  forbidden: {
    title: "You don't have access to generate this bundle",
    description: "Ask a tenant owner or admin to grant audit-read access.",
  },
  payload: {
    title: "The requested period could not be processed",
    description: "Check the From/To dates and try again.",
  },
  cursor: {
    title: "The bundle walk was interrupted",
    description: "Start a fresh generation with the button below.",
  },
  timeout: {
    title: "Generating the bundle timed out",
    description: "Try a shorter period, or try again shortly.",
  },
  "too-large": {
    title: "This period is too large to generate here",
    description: "Choose a shorter period and try again.",
  },
  unknown: { title: "Something went wrong generating the bundle" },
};

async function fetchPage(
  since: string,
  until: string,
  bundleToken?: string,
): Promise<Art12BundleResponse> {
  const params = new URLSearchParams({ since, until });
  if (bundleToken) params.set("bundle_token", bundleToken);
  return bffGet<Art12BundleResponse>(`/admin/compliance/art12-bundle?${params.toString()}`);
}

export function ComplianceReportCenter() {
  const [sinceInput, setSinceInput] = useState("");
  const [untilInput, setUntilInput] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [walkState, setWalkState] = useState<WalkState>("idle");
  const [assembledBundle, setAssembledBundle] = useState<Art12AssembledBundle | null>(null);
  const [errorKind, setErrorKind] = useState<ErrorKind | null>(null);

  async function handleGenerate() {
    if (walkState === "generating") return; // R8: a second click while in flight is a no-op
    setFieldError(null);
    if (!sinceInput || !untilInput) return;

    const sinceDate = new Date(sinceInput);
    const untilDate = new Date(untilInput);
    if (sinceDate.getTime() > untilDate.getTime()) {
      // R1: client-side validation blocks the call entirely — zero fetches.
      setFieldError("From must be on or before To.");
      return;
    }

    setWalkState("generating");
    setErrorKind(null);
    setAssembledBundle(null);
    try {
      const result = await assembleArt12Bundle(
        fetchPage,
        sinceDate.toISOString(),
        untilDate.toISOString(),
      );
      setAssembledBundle(result);
      setWalkState("done");
    } catch (err) {
      // Fail closed: discard ANY partial state, never render a partial preview.
      setAssembledBundle(null);
      setErrorKind(classifyError(err));
      setWalkState("error");
    }
  }

  function handleDownload() {
    if (!assembledBundle) return;
    const blob = new Blob([JSON.stringify(assembledBundle)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const sinceDate = assembledBundle.cover.period.since.slice(0, 10);
    const untilDate = assembledBundle.cover.period.until.slice(0, 10);
    const filename = `art12-bundle-${assembledBundle.cover.tenant_id}-${sinceDate}-${untilDate}.json`;
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  // A 403 is fundamentally un-retryable from this control — replace the whole
  // surface (no picker, no form) rather than inviting a retry that can never succeed.
  if (walkState === "error" && errorKind === "forbidden") {
    return (
      <ErrorState
        title={ERROR_COPY.forbidden.title}
        description={ERROR_COPY.forbidden.description}
      />
    );
  }

  const generateLabel =
    walkState === "generating"
      ? "Generating…"
      : errorKind === "cursor"
        ? "Generate again"
        : "Generate";

  return (
    <div className="flex flex-col gap-6">
      {/* Mounted under the page's own <h1> (SettingsPage's PageHeader) — this h2 is the
          intervening level so the component's own <h3> subsection titles (below) never
          skip from h1 straight to h3 (WCAG 2.2 heading-level-skip a11y rule). */}
      <h2 className="text-lg font-semibold text-foreground">Compliance reporting</h2>

      <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="art12-since" className="text-xs font-medium text-muted-foreground">
              From
            </label>
            <Input
              id="art12-since"
              type="datetime-local"
              value={sinceInput}
              disabled={walkState === "generating"}
              onChange={(e) => setSinceInput(e.target.value)}
              className="w-56"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="art12-until" className="text-xs font-medium text-muted-foreground">
              To
            </label>
            <Input
              id="art12-until"
              type="datetime-local"
              value={untilInput}
              disabled={walkState === "generating"}
              onChange={(e) => setUntilInput(e.target.value)}
              className="w-56"
            />
          </div>
          <Button
            type="button"
            disabled={!sinceInput || !untilInput || walkState === "generating"}
            onClick={() => {
              void handleGenerate();
            }}
          >
            {generateLabel}
          </Button>
        </div>
        {fieldError && (
          <p role="alert" aria-live="polite" className="text-sm text-destructive">
            {fieldError}
          </p>
        )}
      </div>

      {walkState === "error" && errorKind && (
        <ErrorState title={ERROR_COPY[errorKind].title} description={ERROR_COPY[errorKind].description} />
      )}

      {walkState === "done" && assembledBundle && (
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
          <div className="flex items-center justify-between gap-4">
            <h3 className="text-sm font-semibold text-foreground">Art. 12 record-keeping bundle</h3>
            <BundleEvidenceSeal />
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Bundle ID</dt>
            <dd className="text-foreground">{assembledBundle.cover.bundle_id}</dd>

            <dt className="text-muted-foreground">Generated at</dt>
            <dd className="text-foreground">{formatTimestamp(assembledBundle.cover.generated_at)}</dd>

            <dt className="text-muted-foreground">Tenant</dt>
            <dd className="text-foreground">
              {assembledBundle.cover.tenant_name} ({assembledBundle.cover.tenant_id})
            </dd>

            <dt className="text-muted-foreground">Period</dt>
            <dd className="text-foreground">
              {formatTimestamp(assembledBundle.cover.period.since)} –{" "}
              {formatTimestamp(assembledBundle.cover.period.until)}
            </dd>

            <dt className="text-muted-foreground">Residency pin</dt>
            <dd className="text-foreground">{assembledBundle.cover.residency_pin ?? "—"}</dd>

            <dt className="text-muted-foreground">Zero-Data-Retention</dt>
            <dd className="text-foreground">
              {assembledBundle.cover.zdr_state.enabled
                ? `Enabled since ${formatTimestamp(assembledBundle.cover.zdr_state.enabled_at)}`
                : "Disabled"}
            </dd>

            <dt className="text-muted-foreground">Retention window</dt>
            <dd className="text-foreground">
              {assembledBundle.cover.retention_window_days !== null
                ? `${assembledBundle.cover.retention_window_days} days`
                : "—"}
            </dd>

            <dt className="text-muted-foreground">Guardrail configs</dt>
            <dd className="text-foreground">
              {formatNumber(Object.keys(assembledBundle.cover.guardrail_configs_snapshot).length)}{" "}
              configured
            </dd>

            <dt className="text-muted-foreground">Default tier</dt>
            <dd className="text-foreground">{assembledBundle.cover.default_tier}</dd>

            <dt className="text-muted-foreground">Format version</dt>
            <dd className="text-foreground">{assembledBundle.cover.format_version}</dd>
          </dl>

          <div className="flex flex-col gap-1 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Audit events</span>
              <span className="tabular-nums text-foreground">
                {formatNumber(assembledBundle.sections.audit_events.items.length)}
              </span>
            </div>
            {assembledBundle.sections.audit_events.note && (
              <p className="text-xs text-muted-foreground">
                {assembledBundle.sections.audit_events.note}
              </p>
            )}

            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Request log metadata</span>
              <span className="tabular-nums text-foreground">
                {formatNumber(assembledBundle.sections.request_log_metadata.items.length)}
              </span>
            </div>
            {assembledBundle.sections.request_log_metadata.note && (
              <p className="text-xs text-muted-foreground">
                {assembledBundle.sections.request_log_metadata.note}
              </p>
            )}

            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Usage lineage</span>
              <span className="tabular-nums text-foreground">
                {formatNumber(assembledBundle.sections.usage_lineage.items.length)}
              </span>
            </div>
            {assembledBundle.sections.usage_lineage.note && (
              <p className="text-xs text-muted-foreground">
                {assembledBundle.sections.usage_lineage.note}
              </p>
            )}
          </div>

          <div>
            <Button type="button" variant="secondary" onClick={handleDownload}>
              Download bundle (JSON)
            </Button>
          </div>
        </div>
      )}

      <ScheduleControl />

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold text-foreground">Generated reports</h3>
        <GeneratedReportsList />
      </div>
    </div>
  );
}
