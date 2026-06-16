/**
 * console-spend-redesign.test.tsx — RED suite for SpendPage's v23 block adoption.
 *
 * Frozen §3 (presentation-only): SpendPage totals render via StatCard tiles (each keeping its
 * totals-* test hook) and the spend-by-key / spend-by-team breakdowns render via DataTable —
 * with every BFF route, queryKey, field, and frozen hook (totals-*, Totals ({window}) heading,
 * sm:grid-cols-4, getByRole("table",{name:/spend by key/i})) byte-identical.
 *
 * TRUE-RED REASON: SpendPage still renders the totals as a hand-rolled <dl> and the breakdown
 * as a raw <Table>, so neither carries data-slot="stat-card" / data-slot="data-table" until
 * Build adopts StatCard + DataTable.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { SpendPage } from "@/components/spend/SpendPage";

function Wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const TOTALS = {
  bucket_start: "2026-06-01T00:00:00Z",
  bucket_end: "2026-06-30T00:00:00Z",
  requests: 3,
  prompt_tokens: 300,
  completion_tokens: 150,
  cost_usd: "1.23",
};

const BUCKETS = [
  { bucket_start: "2026-06-01T00:00:00Z", requests: 2, prompt_tokens: 200, completion_tokens: 100, cost_usd: "0.80" },
  { bucket_start: "2026-06-08T00:00:00Z", requests: 1, prompt_tokens: 100, completion_tokens: 50, cost_usd: "0.43" },
];

const KEY_BREAKDOWN = [
  { key_id: "key-001", requests: 3, prompt_tokens: 300, completion_tokens: 150, cost_usd: "3.50" },
];

function installSpendHandlers() {
  server.use(
    http.get("http://localhost:3000/api/gw/admin/keys", () => HttpResponse.json([])),
    http.get("http://localhost:3000/api/gw/admin/spend", ({ request }) => {
      const url = new URL(request.url);
      const groupBy = url.searchParams.get("group_by");
      return HttpResponse.json({
        window: "month",
        bucket_size: "month",
        totals: TOTALS,
        buckets: BUCKETS,
        breakdown: groupBy === "key_id" ? KEY_BREAKDOWN : null,
      });
    }),
  );
}

describe("SpendPage — v23 block adoption", () => {
  it("test_spend_totals_render_via_statcards", async () => {
    installSpendHandlers();
    const { container } = render(<SpendPage />, { wrapper: Wrapper });

    const cost = await screen.findByTestId("totals-cost");
    expect(cost).toHaveTextContent("1.23");
    expect(screen.getByTestId("totals-requests")).toHaveTextContent("3");
    // each totals tile is a StatCard
    expect(cost.closest('[data-slot="stat-card"]')).not.toBeNull();
    // the frozen heading + responsive grid hook survive
    expect(screen.getByText(/totals \(month\)/i)).toBeInTheDocument();
    expect(container.querySelector('[class*="sm:grid-cols-4"]')).not.toBeNull();
  });

  it("test_spend_breakdown_renders_via_datatable", async () => {
    installSpendHandlers();
    render(<SpendPage />, { wrapper: Wrapper });

    await screen.findByTestId("totals-cost");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /group by/i }),
      "key_id",
    );

    const table = await screen.findByRole("table", { name: /spend by key/i });
    expect(within(table).getByText("key-001")).toBeInTheDocument();
    expect(within(table).getByText("3.50")).toBeInTheDocument();
    expect(table.closest('[data-slot="data-table"]') ?? table).toHaveAttribute(
      "data-slot",
      "data-table",
    );
  });
});
