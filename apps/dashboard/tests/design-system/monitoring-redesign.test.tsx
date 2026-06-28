/**
 * tests/design-system/monitoring-redesign.test.tsx — RED structural suite.
 *
 * Verifies the monitoring-redesign build contract (spec §3 v1, frozen):
 *  - PageHeader primitive: exactly one h1, description node, actions slot
 *  - Each of 4 monitoring pages: pinned h1, role="tablist", Overview selected by default
 *  - Hero metric regions present on each page
 *  - Usage Trends tab: chart container (data-testid="usage-trend-chart") when records present;
 *    empty records → no chart, no-data note
 *  - SLO overview honest-degrade: "not available yet" availability-trend note, no svg chart
 *  - SLO Latency tab: "not available yet" placeholder, no latency chart
 *  - Health History tab: "not available yet", no history chart
 *
 * RED before build:
 *   import { PageHeader } from "@/components/ui/page-header" → MODULE_NOT_FOUND
 *   All tests in this file fail immediately at module resolution — the correct RED state.
 *
 * Once the builder creates page-header.tsx AND re-composes each monitoring page with
 * Tabs + PageHeader, these tests will turn GREEN without modification.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

// ── RED: this import causes MODULE_NOT_FOUND — entire file fails at load ──────
import { PageHeader } from "@/components/ui/page-header";

// Existing page components (already built; tabs not yet added)
import { UsagePage } from "@/components/usage/UsagePage";
import { SpendPage } from "@/components/spend/SpendPage";
import { SloPage } from "@/components/slo/SloPage";
import { HealthPage } from "@/components/health/HealthPage";

const APP = "http://localhost:3000";

// ── MSW response fixtures (shapes from existing frozen contracts) ─────────────

const USAGE_NOMINAL = {
  total_cost_usd: "4.56",
  total_requests: 7,
  total_prompt_tokens: 700,
  total_completion_tokens: 350,
  records: [
    {
      id: "00000000-0000-0000-0000-000000000001",
      model_id: "openai/gpt-4o",
      prompt_tokens: 100,
      completion_tokens: 50,
      cost_usd: "4.56",
      status: 200,
      created_at: "2026-06-10T00:00:00Z",
    },
  ],
};

const USAGE_EMPTY = {
  total_cost_usd: "0",
  total_requests: 0,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  records: [],
};

const MODELS_RESPONSE = {
  object: "list",
  data: [
    {
      id: "openai/gpt-4o",
      name: "GPT-4o",
      context_length: 128000,
      prompt_per_token: 0.000003,
      completion_per_token: 0.000012,
      object: "model",
    },
  ],
};

const BUDGET_RESPONSE = {
  budget_usd_monthly: "50.00",
  spent_usd_month: "4.56",
};

const SPEND_NOMINAL = {
  window: "month",
  bucket_size: "month",
  totals: {
    bucket_start: "2026-06-01T00:00:00Z",
    bucket_end: "2026-06-28T00:00:00Z",
    requests: 10,
    prompt_tokens: 1000,
    completion_tokens: 500,
    cost_usd: "7.89",
  },
  buckets: [
    {
      bucket_start: "2026-06-01T00:00:00Z",
      requests: 10,
      prompt_tokens: 1000,
      completion_tokens: 500,
      cost_usd: "7.89",
    },
  ],
  breakdown: null,
};

const KEYS_RESPONSE = [
  { key_id: "key-001", name: "prod", prefix: "sk-1a2b" },
];

const SLO_NOMINAL = {
  window_hours: 24,
  total_requests: 100,
  success_count: 95,
  client_error_count: 3,
  server_error_count: 2,
  availability: 0.95,
  error_rate: 0.05,
  latency_ms: null,
};

const HEALTH_NOMINAL = {
  checked_at: "2026-06-28T10:00:00Z",
  upstreams: [
    {
      name: "openrouter",
      status: "up",
      last_event_at: "2026-06-28T09:55:00Z",
      last_event_type: "upstream_health_recovered",
    },
  ],
};

// ── Harness helpers ───────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>
  );
}

function useUsageHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/usage`, () => HttpResponse.json(USAGE_NOMINAL)),
    http.get(`${APP}/api/gw/admin/catalog/models`, () => HttpResponse.json(MODELS_RESPONSE)),
    http.get(`${APP}/api/gw/admin/budget`, () => HttpResponse.json(BUDGET_RESPONSE)),
  );
}

function useSpendHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/spend`, () => HttpResponse.json(SPEND_NOMINAL)),
    http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json(KEYS_RESPONSE)),
  );
}

function useSloHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/slo`, () => HttpResponse.json(SLO_NOMINAL)),
  );
}

function useHealthHandlers() {
  server.use(
    http.get(`${APP}/api/gw/admin/health/upstreams`, () => HttpResponse.json(HEALTH_NOMINAL)),
  );
}

// ── PageHeader primitive ──────────────────────────────────────────────────────

describe("PageHeader primitive", () => {
  it("test_page_header_renders_exactly_one_h1_with_title", () => {
    render(<PageHeader title="Dashboard Overview" />);

    const h1 = screen.getByRole("heading", { level: 1, name: "Dashboard Overview" });
    expect(h1).toBeInTheDocument();
    // Exactly one h1 — spec: "EXACTLY ONE h1"
    expect(document.querySelectorAll("h1")).toHaveLength(1);
  });

  it("test_page_header_renders_description_node", () => {
    render(
      <PageHeader
        title="Usage & Cost Analytics"
        description="Track spend, tokens, and budget across your tenant"
      />,
    );
    expect(
      screen.getByText("Track spend, tokens, and budget across your tenant"),
    ).toBeInTheDocument();
  });

  it("test_page_header_renders_actions_slot_child", () => {
    render(
      <PageHeader
        title="My Page"
        actions={<button type="button">Edit Budget</button>}
      />,
    );
    expect(screen.getByRole("button", { name: "Edit Budget" })).toBeInTheDocument();
  });

  it("test_page_header_titleId_sets_id_on_h1", () => {
    render(<PageHeader title="Service levels" titleId="slo-heading" />);
    const h1 = screen.getByRole("heading", { level: 1, name: "Service levels" });
    expect(h1).toHaveAttribute("id", "slo-heading");
  });

  it("test_page_header_no_description_omits_description_node", () => {
    render(<PageHeader title="Upstream Health" />);
    // No description = no <p> after h1
    expect(screen.queryByRole("paragraph")).not.toBeInTheDocument();
  });
});

// ── UsagePage structural ──────────────────────────────────────────────────────

describe("UsagePage — monitoring redesign", () => {
  it("test_usage_page_h1_pinned_to_spec", async () => {
    useUsageHandlers();
    render(<UsagePage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /usage & cost analytics/i }),
      ).toBeInTheDocument();
    });
  });

  it("test_usage_page_has_tablist_with_overview_selected_by_default", async () => {
    useUsageHandlers();
    render(<UsagePage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");
  });

  it("test_usage_page_tab_labels_overview_records_catalog_trends", async () => {
    useUsageHandlers();
    render(<UsagePage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /records/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /catalog/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /trends/i })).toBeInTheDocument();
  });

  it("test_usage_page_hero_metric_total_cost_present", async () => {
    useUsageHandlers();
    render(<UsagePage />, { wrapper: Wrapper });

    // Hero region carries the headline total cost (total_cost_usd = "4.56")
    const hero = await screen.findByTestId("usage-hero");
    expect(within(hero).getByText(/4\.56/)).toBeInTheDocument();
  });

  it("test_usage_trends_tab_reveals_usage_trend_chart", async () => {
    const user = userEvent.setup();
    useUsageHandlers();
    render(<UsagePage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /trends/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("tab", { name: /trends/i }));

    // chart container present — data-testid="usage-trend-chart" (frozen spec)
    await waitFor(() => {
      expect(screen.getByTestId("usage-trend-chart")).toBeInTheDocument();
    });
  });

  it("test_usage_trends_empty_records_shows_no_data_note_no_chart", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/usage`, () => HttpResponse.json(USAGE_EMPTY)),
      http.get(`${APP}/api/gw/admin/catalog/models`, () => HttpResponse.json(MODELS_RESPONSE)),
      http.get(`${APP}/api/gw/admin/budget`, () => HttpResponse.json(BUDGET_RESPONSE)),
    );
    render(<UsagePage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /trends/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("tab", { name: /trends/i }));

    // No chart when records empty
    await waitFor(() => {
      expect(screen.queryByTestId("usage-trend-chart")).not.toBeInTheDocument();
    });

    // A no-data/empty note is present instead
    expect(
      screen.getByText(/no data|no records|no trend|nothing to show/i),
    ).toBeInTheDocument();
  });

  it("test_usage_page_axe_clean_on_overview", async () => {
    useUsageHandlers();
    const { container } = render(<UsagePage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /usage & cost analytics/i }),
      ).toBeInTheDocument();
    });

    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });
});

// ── SpendPage structural ──────────────────────────────────────────────────────

describe("SpendPage — monitoring redesign", () => {
  it("test_spend_page_h1_pinned_to_spec", async () => {
    useSpendHandlers();
    render(<SpendPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /spend analytics/i }),
      ).toBeInTheDocument();
    });
  });

  it("test_spend_page_has_tablist_with_overview_selected_by_default", async () => {
    useSpendHandlers();
    render(<SpendPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");
  });

  it("test_spend_page_tab_labels_overview_breakdown", async () => {
    useSpendHandlers();
    render(<SpendPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /breakdown/i })).toBeInTheDocument();
  });

  it("test_spend_page_hero_metric_cost_for_window", async () => {
    useSpendHandlers();
    render(<SpendPage />, { wrapper: Wrapper });

    // Hero region carries the window cost (totals.cost_usd = "7.89")
    const hero = await screen.findByTestId("spend-hero");
    expect(within(hero).getByText(/7\.89/)).toBeInTheDocument();
  });
});

// ── SloPage structural ────────────────────────────────────────────────────────

describe("SloPage — monitoring redesign", () => {
  it("test_slo_page_h1_pinned_to_spec", async () => {
    useSloHandlers();
    render(<SloPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /service levels/i }),
      ).toBeInTheDocument();
    });
  });

  it("test_slo_page_has_tablist_with_overview_selected_by_default", async () => {
    useSloHandlers();
    render(<SloPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");
  });

  it("test_slo_page_tab_labels_overview_latency", async () => {
    useSloHandlers();
    render(<SloPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /latency/i })).toBeInTheDocument();
  });

  it("test_slo_page_hero_metric_availability_present", async () => {
    useSloHandlers();
    render(<SloPage />, { wrapper: Wrapper });

    // Hero region carries the availability headline (availability 0.95 → 95%)
    const hero = await screen.findByTestId("slo-hero");
    expect(within(hero).getByText(/95(\.0)?%/)).toBeInTheDocument();
  });

  it("test_slo_overview_availability_trend_not_available_yet_no_chart", async () => {
    useSloHandlers();
    render(<SloPage />, { wrapper: Wrapper });

    // Overview panel: muted "not available yet" note for availability trend (latency_ms null)
    await waitFor(() => {
      expect(screen.getByText(/not available yet/i)).toBeInTheDocument();
    });

    // No chart svg rendered for the availability trend
    expect(screen.queryByTestId("availability-trend-chart")).not.toBeInTheDocument();
  });

  it("test_slo_latency_tab_not_available_yet_no_chart_svg", async () => {
    const user = userEvent.setup();
    useSloHandlers();
    render(<SloPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /latency/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("tab", { name: /latency/i }));

    // Latency panel: honest-degrade placeholder
    await waitFor(() => {
      expect(screen.getByText(/not available yet/i)).toBeInTheDocument();
    });

    // No latency chart svg — latency_ms is null, never fabricate
    expect(screen.queryByTestId("latency-chart")).not.toBeInTheDocument();
  });
});

// ── HealthPage structural ─────────────────────────────────────────────────────

describe("HealthPage — monitoring redesign", () => {
  it("test_health_page_h1_pinned_to_spec", async () => {
    useHealthHandlers();
    render(<HealthPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { level: 1, name: /upstream health/i }),
      ).toBeInTheDocument();
    });
  });

  it("test_health_page_has_tablist_with_overview_selected_by_default", async () => {
    useHealthHandlers();
    render(<HealthPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");
  });

  it("test_health_page_tab_labels_overview_history", async () => {
    useHealthHandlers();
    render(<HealthPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tablist")).toBeInTheDocument();
    });

    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /history/i })).toBeInTheDocument();
  });

  it("test_health_page_hero_up_down_summary_present", async () => {
    useHealthHandlers();
    render(<HealthPage />, { wrapper: Wrapper });

    // Hero region carries an up/down summary (1 of 1 upstream up → "healthy"/"up" + a count)
    const hero = await screen.findByTestId("health-hero");
    expect(within(hero).getByText(/up|healthy|operational/i)).toBeInTheDocument();
  });

  it("test_health_history_tab_not_available_yet_no_chart", async () => {
    const user = userEvent.setup();
    useHealthHandlers();
    render(<HealthPage />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /history/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("tab", { name: /history/i }));

    // History panel: honest-degrade — history data not available yet
    await waitFor(() => {
      expect(screen.getByText(/not available yet/i)).toBeInTheDocument();
    });

    // No history chart
    expect(screen.queryByTestId("health-history-chart")).not.toBeInTheDocument();
  });
});
