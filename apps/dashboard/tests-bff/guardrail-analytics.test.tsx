/**
 * tests-bff/guardrail-analytics.test.tsx — RED suite for guardrail-analytics (TASK.md §4,
 * scenario M8: "the dashboard exposes a new Guardrail Analytics page").
 *
 * Structural clone of console-spend-redesign.test.tsx + spend-chart.test.tsx for the new
 * GuardrailAnalyticsPage — mirrors SpendPage's exact IA (PageHeader selects, StatCard
 * hero, Recharts sparkline, controlled Tabs + DataTable breakdown).
 *
 * TRUE-RED REASON: GuardrailAnalyticsPage / GuardrailAnalyticsSparkline do not exist yet
 * -> `from "@/components/guardrails/GuardrailAnalyticsPage"` raises a module-resolution
 * error at collection time.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { axe } from "@/test-support/axe";
import { server } from "./mocks/server";
import React from "react";

import { GuardrailAnalyticsPage } from "@/components/guardrails/GuardrailAnalyticsPage";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

const ZERO_COUNTS = {
  evaluations: 0,
  hits: 0,
  blocked: 0,
  masked: 0,
  audited: 0,
  passed: 0,
  error: 0,
  unchecked: 0,
  budget_exceeded: 0,
};

const TOTALS = {
  bucket_start: "2026-06-01T00:00:00Z",
  bucket_end: "2026-06-30T00:00:00Z",
  evaluations: 10,
  hits: 4,
  blocked: 2,
  masked: 1,
  audited: 1,
  passed: 6,
  error: 0,
  unchecked: 0,
  budget_exceeded: 0,
};

const BUCKETS = [
  { bucket_start: "2026-06-01T00:00:00Z", ...ZERO_COUNTS, evaluations: 6, passed: 4, blocked: 2 },
  { bucket_start: "2026-06-08T00:00:00Z", ...ZERO_COUNTS, evaluations: 4, passed: 2, masked: 1, audited: 1 },
];

const GUARDRAIL_BREAKDOWN = [
  { guardrail: "prompt_injection", ...ZERO_COUNTS, evaluations: 6, blocked: 2, passed: 4 },
  { guardrail: "pii_mask", ...ZERO_COUNTS, evaluations: 4, masked: 1, audited: 1, passed: 2 },
];

const ZERO_TOTALS = { ...ZERO_COUNTS, bucket_start: "2026-06-01T00:00:00Z", bucket_end: "2026-06-30T00:00:00Z" };

function installHandlers(opts: { totals?: typeof TOTALS; buckets?: typeof BUCKETS } = {}) {
  server.use(
    http.get("http://localhost:3000/api/gw/admin/keys", () => HttpResponse.json([])),
    http.get("http://localhost:3000/api/gw/admin/guardrails/analytics", ({ request }) => {
      const url = new URL(request.url);
      const groupBy = url.searchParams.get("group_by");
      return HttpResponse.json({
        window: "month",
        bucket_size: "month",
        totals: opts.totals ?? TOTALS,
        buckets: opts.buckets ?? BUCKETS,
        breakdown: groupBy === "guardrail" ? GUARDRAIL_BREAKDOWN : null,
      });
    }),
  );
}

describe("GuardrailAnalyticsPage", () => {
  it("test_totals_render_via_statcards", async () => {
    installHandlers();
    const { container } = render(<GuardrailAnalyticsPage />, { wrapper: Wrapper });

    const evaluations = await screen.findByTestId("totals-evaluations");
    expect(evaluations).toHaveTextContent("10");
    expect(screen.getByTestId("totals-hits")).toHaveTextContent("4");
    expect(screen.getByTestId("totals-blocked")).toHaveTextContent("2");
    // each totals tile is a StatCard
    expect(evaluations.closest('[data-slot="stat-card"]')).not.toBeNull();
    expect(screen.getByText(/totals \(month\)/i)).toBeInTheDocument();
    expect(container.querySelector('[class*="sm:grid-cols-4"]')).not.toBeNull();
  });

  it("test_sparkline_chart_accessible", async () => {
    installHandlers();
    render(<GuardrailAnalyticsPage />, { wrapper: Wrapper });

    const figure = await screen.findByTestId("guardrail-analytics-chart");
    expect(figure).toBeInTheDocument();
    expect(figure).toHaveAccessibleName(/guardrail evaluations over time/i);
    expect(within(figure).getByText(/evaluations over time/i)).toBeInTheDocument();

    // fallback: the bucket list + totals leaves remain the accessible data source
    expect(screen.getAllByTestId("guardrail-analytics-bucket").length).toBe(2);
    expect(screen.getByTestId("totals-evaluations")).toHaveTextContent("10");
  });

  it("test_chart_absent_when_empty", async () => {
    installHandlers({ totals: ZERO_TOTALS, buckets: [] });
    render(<GuardrailAnalyticsPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("guardrail-analytics-zero-state")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("guardrail-analytics-chart")).not.toBeInTheDocument();
  });

  it("test_breakdown_renders_via_datatable_for_group_by_guardrail", async () => {
    installHandlers();
    render(<GuardrailAnalyticsPage />, { wrapper: Wrapper });

    await screen.findByTestId("totals-evaluations");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /group by/i }),
      "guardrail",
    );

    const table = await screen.findByRole("table", { name: /guardrail hits by pattern/i });
    expect(within(table).getByText("prompt_injection")).toBeInTheDocument();
    expect(within(table).getByText("pii_mask")).toBeInTheDocument();
    expect(table.closest('[data-slot="data-table"]') ?? table).toHaveAttribute(
      "data-slot",
      "data-table",
    );
  });

  it("test_guardrail_analytics_axe_clean", async () => {
    installHandlers();
    const { container } = render(<GuardrailAnalyticsPage />, { wrapper: Wrapper });

    await screen.findByTestId("totals-evaluations");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});
