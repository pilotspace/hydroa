/**
 * console-surfaces-redesign.test.tsx — RED suite for the v23 console-surfaces-redesign task.
 *
 * Frozen §3 (presentation-only): Usage · Spend render their KPIs via StatCard, their flat
 * tables via DataTable, and Spend's chart via ChartContainer — WITHOUT changing any data seam
 * or losing a frozen test hook. This file drives the design-system seams (StatCard.valueTestId
 * + data-slot, DataTable.ariaLabel + data-slot) and the prop-driven surface components
 * (UsageStatsCards, UsageTable, BudgetWidget, SpendSparkline). SpendPage's integration is
 * driven separately in tests-bff/console-spend-redesign.test.tsx.
 *
 * TRUE-RED REASON: StatCard/DataTable do not yet emit data-slot / honour valueTestId|ariaLabel,
 * and the surfaces still render hand-rolled tiles/tables — so the [data-slot="…"] and sortable-
 * header / accessible-name assertions fail until Build adopts the shared blocks.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { ColumnDef } from "@tanstack/react-table";

import { StatCard, DataTable } from "@/components/ui";
import { UsageStatsCards, type UsageData } from "@/components/usage/UsageStatsCards";
import { UsageTable } from "@/components/usage/UsageTable";
import { BudgetWidget } from "@/components/usage/BudgetWidget";
import { SpendSparkline } from "@/components/spend/SpendSparkline";

// recharts (SpendSparkline) renders an SVG; jsdom lacks these — shim defensively.
beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

const USAGE: UsageData = {
  total_cost_usd: "1.23",
  total_requests: 3,
  total_prompt_tokens: 300,
  total_completion_tokens: 150,
  records: [
    {
      id: "r1",
      model_id: "openai/gpt-4o",
      prompt_tokens: 100,
      completion_tokens: 50,
      cost_usd: "1.23",
      status: 200,
      created_at: "2026-06-01",
    },
    {
      id: "r2",
      model_id: "anthropic/claude-3-5-sonnet",
      prompt_tokens: 200,
      completion_tokens: 100,
      cost_usd: "4.56",
      status: 200,
      created_at: "2026-06-02",
    },
  ],
};

describe("StatCard — value test hook + slot", () => {
  it("test_stat_card_value_testid_and_slot", () => {
    const { container } = render(
      <StatCard label="Cost (USD)" value="1.23" valueTestId="totals-cost" />,
    );
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("1.23");
    expect(container.querySelector('[data-slot="stat-card"]')).not.toBeNull();
  });
});

describe("DataTable — accessible name + slot", () => {
  const columns: ColumnDef<{ key_id: string; cost_usd: string }>[] = [
    { accessorKey: "key_id", header: "Key" },
    { accessorKey: "cost_usd", header: "Cost (USD)" },
  ];

  it("test_data_table_aria_label_and_slot", () => {
    const { container } = render(
      <DataTable
        columns={columns}
        data={[{ key_id: "key-001", cost_usd: "3.50" }]}
        ariaLabel="Spend by key"
      />,
    );
    expect(screen.getByRole("table", { name: /spend by key/i })).toBeInTheDocument();
    expect(container.querySelector('[data-slot="data-table"]')).not.toBeNull();
  });
});

describe("UsageStatsCards — via StatCard", () => {
  it("test_usage_stats_render_via_statcard", () => {
    const { container } = render(
      <UsageStatsCards isLoading={false} isError={false} error={null} data={USAGE} />,
    );
    expect(container.querySelectorAll('[data-slot="stat-card"]').length).toBe(4);
    expect(container.querySelector('[class*="sm:grid-cols-4"]')).not.toBeNull();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();
    expect(screen.getByText("1.23")).toBeInTheDocument();
  });

  it("test_usage_stats_loading_error_render_no_tile", () => {
    const { container, rerender } = render(
      <UsageStatsCards isLoading={true} isError={false} error={null} data={undefined} />,
    );
    expect(screen.getByTestId("loading")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="stat-card"]').length).toBe(0);

    rerender(
      <UsageStatsCards
        isLoading={false}
        isError={true}
        error={new Error("boom")}
        data={undefined}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="stat-card"]').length).toBe(0);
  });
});

describe("UsageTable — via sortable DataTable", () => {
  it("test_usage_records_render_via_datatable", () => {
    const { container } = render(
      <UsageTable isLoading={false} isError={false} data={USAGE} />,
    );
    expect(container.querySelector('[data-slot="data-table"]')).not.toBeNull();
    // sortable headers render as keyboard-operable buttons
    expect(screen.getByRole("button", { name: /cost/i })).toBeInTheDocument();
    // every record's text is byte-identical (data seam preserved)
    expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude-3-5-sonnet")).toBeInTheDocument();
    expect(screen.getByText("4.56")).toBeInTheDocument();
  });

  it("test_usage_records_loading_error_zero_rows", () => {
    const { rerender } = render(
      <UsageTable isLoading={true} isError={false} data={undefined} />,
    );
    expect(screen.queryAllByRole("row").length).toBe(0);

    rerender(<UsageTable isLoading={false} isError={true} data={undefined} />);
    expect(screen.queryAllByRole("row").length).toBe(0);

    rerender(
      <UsageTable
        isLoading={false}
        isError={false}
        data={{ ...USAGE, records: [] }}
      />,
    );
    expect(screen.getByText(/no usage records yet/i)).toBeInTheDocument();
  });
});

describe("BudgetWidget — via StatCards, edit preserved", () => {
  it("test_budget_renders_via_statcards", () => {
    const { container, rerender } = render(
      <BudgetWidget
        isLoading={false}
        isError={false}
        error={null}
        data={{ budget_usd_monthly: "25.00", spent_usd_month: "10.50" }}
        canEdit={true}
      />,
    );
    expect(container.querySelectorAll('[data-slot="stat-card"]').length).toBe(2);
    expect(screen.getByText("25.00")).toBeInTheDocument();
    expect(screen.getByText("10.50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /edit budget/i })).toBeInTheDocument();

    // member: no edit affordance
    rerender(
      <BudgetWidget
        isLoading={false}
        isError={false}
        error={null}
        data={{ budget_usd_monthly: "25.00", spent_usd_month: "10.50" }}
        canEdit={false}
      />,
    );
    expect(screen.queryByRole("button", { name: /edit budget/i })).toBeNull();

    // null ceiling → Unlimited
    rerender(
      <BudgetWidget
        isLoading={false}
        isError={false}
        error={null}
        data={{ budget_usd_monthly: null, spent_usd_month: "0.00" }}
        canEdit={true}
      />,
    );
    expect(screen.getByText(/unlimited/i)).toBeInTheDocument();
  });
});

describe("SpendSparkline — wraps a ChartContainer in the figure", () => {
  it("test_spend_chart_wraps_chart_container", () => {
    render(
      <SpendSparkline
        buckets={[
          { bucket_start: "2026-06-01", cost_usd: "1.00" },
          { bucket_start: "2026-06-02", cost_usd: "2.00" },
        ]}
      />,
    );
    const figure = screen.getByTestId("spend-chart");
    expect(within(figure).getByText(/spend over time/i)).toBeInTheDocument();
    expect(figure.querySelector('[data-slot="chart"]')).not.toBeNull();
  });

  it("test_spend_chart_absent_when_empty", () => {
    const { container } = render(<SpendSparkline buckets={[]} />);
    expect(container.querySelector('[data-testid="spend-chart"]')).toBeNull();
  });
});
