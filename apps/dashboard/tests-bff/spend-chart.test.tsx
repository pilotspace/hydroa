/**
 * spend-chart.test.tsx — RED suite for usage-cost-ui (v13 task 2)
 *
 * The frozen §3 NEW CHART RENDER CONTRACT (additive, SpendPage only):
 *   - SpendPage renders an accessible Recharts chart derived ONLY from buckets[]
 *   - wrapper: <figure data-testid="spend-chart" aria-label="Spend over time"> + a title
 *   - the existing spend-bucket <li> list + totals-* leaves stay rendered
 *     (the chart is NEVER the sole source of a datum)
 *   - buckets.length === 0 -> chart NOT rendered (zero-state path unchanged)
 *
 * TRUE-RED REASON: SpendPage does not yet mount a <figure data-testid="spend-chart">,
 * so getByTestId("spend-chart") throws until Build adds SpendSparkline + mounts it.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { SpendPage } from "@/components/spend/SpendPage";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

const SPEND_MONTH_FIXTURE = {
  window: "month",
  bucket_size: "month",
  totals: {
    bucket_start: "2026-06-01T00:00:00Z",
    bucket_end: "2026-06-11T00:00:00Z",
    requests: 3,
    prompt_tokens: 300,
    completion_tokens: 150,
    cost_usd: "1.23",
  },
  buckets: [
    {
      bucket_start: "2026-06-01T00:00:00Z",
      requests: 2,
      prompt_tokens: 200,
      completion_tokens: 100,
      cost_usd: "0.80",
    },
    {
      bucket_start: "2026-06-08T00:00:00Z",
      requests: 1,
      prompt_tokens: 100,
      completion_tokens: 50,
      cost_usd: "0.43",
    },
  ],
  breakdown: null,
};

const SPEND_ZERO_FIXTURE = {
  window: "month",
  bucket_size: "month",
  totals: {
    bucket_start: "2026-06-01T00:00:00Z",
    bucket_end: "2026-06-11T00:00:00Z",
    requests: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: "0.00",
  },
  buckets: [],
  breakdown: null,
};

describe("SpendPage — accessible spend-over-time chart (additive)", () => {
  /**
   * test_spend_chart_accessible
   * Given admin-spend resolves with >=2 buckets
   * When SpendPage renders
   * Then a <figure data-testid="spend-chart" aria-label> with a title is present
   * And the spend-bucket list + totals-* leaves still render (chart not sole source)
   */
  it("test_spend_chart_accessible", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
        HttpResponse.json(SPEND_MONTH_FIXTURE)
      )
    );

    render(<SpendPage />, { wrapper: Wrapper });

    // chart figure present + accessibly labelled
    const figure = await screen.findByTestId("spend-chart");
    expect(figure).toBeInTheDocument();
    expect(figure).toHaveAccessibleName(/spend over time/i);
    // a visible title element inside the figure (figcaption or heading)
    expect(within(figure).getByText(/spend over time/i)).toBeInTheDocument();

    // fallback: the bucket list + totals leaves remain the accessible data source
    expect(screen.getAllByTestId("spend-bucket").length).toBe(2);
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("1.23");
    expect(screen.getByTestId("totals-requests")).toHaveTextContent("3");
  });

  /**
   * test_spend_chart_absent_when_empty
   * Given admin-spend resolves with 0 buckets (zero usage)
   * When SpendPage renders
   * Then spend-chart is NOT rendered and the zero-state path is unchanged
   */
  it("test_spend_chart_absent_when_empty", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/spend", () =>
        HttpResponse.json(SPEND_ZERO_FIXTURE)
      )
    );

    render(<SpendPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("spend-zero-state")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("spend-chart")).not.toBeInTheDocument();
  });
});
