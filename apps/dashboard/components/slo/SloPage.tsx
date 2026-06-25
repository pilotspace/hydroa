"use client";

/**
 * SloPage — the dashboard Service Level Objectives surface (admin-only nav).
 *
 * Reads availability, error-rate, and request volume from GET /admin/slo?window_hours=N
 * (via the BFF catch-all) and renders them as stat cards.
 *
 * Contract (FROZEN § v1):
 *   - h1 "Service levels"
 *   - Window selector: 24h(24) / 7d(168) / 30d(720) → refetch GET /admin/slo?window_hours=
 *   - Cards: Availability (%), Error rate (%), Total requests, Breakdown (success/client/server)
 *   - Latency: "not available yet" placeholder (API latency_ms is null — NEVER fabricate)
 *   - Empty window: total 0 → availability 100%, 0 requests, NO NaN
 *   - A11Y: one h1; ordered headings; status conveyed by TEXT % (not color alone); axe 0 serious/critical
 *
 * Auth guard: proxy.ts handles cookie-presence server-side. The admin-only nav link and the
 * gateway's OPS_READ enforcement keep members out.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api-client";
import { Loading, ErrorState, StatCard } from "@/components/ui";
import { Card, CardContent, CardHeader } from "@/components/ui/card";

// ── API shape ─────────────────────────────────────────────────────────────────

export interface SloData {
  window_hours: number;
  total_requests: number;
  success_count: number;
  client_error_count: number;
  server_error_count: number;
  /** 0..1 float — 0.95 means 95% */
  availability: number;
  /** 0..1 float — 0.05 means 5% */
  error_rate: number;
  /** Always null until a latency column exists — NEVER fabricate a number */
  latency_ms: null;
}

// ── Window options ────────────────────────────────────────────────────────────

interface WindowOption {
  label: string;
  hours: number;
}

const WINDOW_OPTIONS: WindowOption[] = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Convert a 0..1 float to a rounded percentage string e.g. 0.9512 → "95%".
 * Guards against NaN/Infinity (returns "0%" for zero-request windows).
 */
function toPercent(ratio: number): string {
  if (!Number.isFinite(ratio)) return "0%";
  return `${Math.round(ratio * 100)}%`;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function SloPage() {
  const [windowHours, setWindowHours] = useState<number>(24);

  const sloQuery = useQuery<SloData>({
    queryKey: ["admin-slo", windowHours],
    queryFn: () => apiGet<SloData>(`/admin/slo?window_hours=${windowHours}`),
  });

  return (
    <section
      aria-labelledby="slo-heading"
      className="flex flex-col gap-6"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h1
          id="slo-heading"
          className="text-2xl font-semibold tracking-tight text-foreground"
        >
          Service levels
        </h1>

        {/* Window selector — buttons for 24h / 7d / 30d */}
        <div
          role="group"
          aria-label="Time window"
          className="flex items-center gap-1 rounded-md border border-border bg-muted p-1"
        >
          {WINDOW_OPTIONS.map((opt) => (
            <button
              key={opt.hours}
              type="button"
              aria-pressed={windowHours === opt.hours}
              aria-label={opt.label}
              onClick={() => setWindowHours(opt.hours)}
              className={
                "rounded px-3 py-1 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                (windowHours === opt.hours
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {sloQuery.isLoading ? (
        <Loading label="Loading service levels…" />
      ) : sloQuery.isError ? (
        <ErrorState
          title={
            sloQuery.error instanceof Error
              ? sloQuery.error.message
              : "Failed to load service levels"
          }
        />
      ) : sloQuery.data ? (
        <SloMetrics data={sloQuery.data} />
      ) : null}
    </section>
  );
}

// ── SloMetrics sub-component ──────────────────────────────────────────────────

function SloMetrics({ data }: { data: SloData }) {
  const availabilityPct = toPercent(data.availability);
  const errorRatePct = toPercent(data.error_rate);

  return (
    <div className="flex flex-col gap-4">
      {/* Primary KPI row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Availability — text % is authoritative (WCAG 1.4.1: not color alone) */}
        <StatCard
          label="Availability"
          value={availabilityPct}
          valueTestId="slo-availability"
          footer={`Over the last ${data.window_hours}h`}
        />

        {/* Error rate — text % is authoritative */}
        <StatCard
          label="Error rate"
          value={errorRatePct}
          valueTestId="slo-error-rate"
          footer={`Over the last ${data.window_hours}h`}
        />

        {/* Total requests */}
        <StatCard
          label="Total requests"
          value={data.total_requests}
          valueTestId="slo-total-requests"
          footer={`Over the last ${data.window_hours}h`}
        />
      </div>

      {/* Breakdown card */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-foreground">Request breakdown</h2>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-3 gap-4">
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Success</dt>
              <dd className="text-lg font-semibold text-foreground">{data.success_count}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Client errors (4xx)</dt>
              <dd className="text-lg font-semibold text-foreground">{data.client_error_count}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Server errors (5xx)</dt>
              <dd className="text-lg font-semibold text-foreground">{data.server_error_count}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* Latency — HONEST placeholder: never fabricate a number */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-foreground">Latency</h2>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            <span aria-label="Latency data not available yet">not available yet</span>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
