/**
 * tests-bff/overview-home.test.tsx (v23) — RED suite for the Overview home.
 *
 * The new `/` landing aggregates the EXISTING reads (/admin/usage · /admin/spend · /admin/budget)
 * — NO new BFF route, NO new dep — into ≥4 KPI StatCards (trend deltas), a usage-over-time
 * ChartCard with a day/week/month range toggle, and a recent-activity DataTable.
 *
 * RED before Build: `@/components/overview/OverviewPage` does not exist (MODULE_NOT_FOUND at
 * collect — the established true-red), and app/page.tsx still redirects `/` unconditionally.
 *
 * Convention: MSW same-origin handlers on `${APP}/api/gw/...` (apiGet routes through /api/gw),
 * QueryClientProvider(retry:false), axe serious|critical with color-contrast disabled (jsdom).
 */
import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

import { OverviewPage } from "@/components/overview/OverviewPage";

const APP = "http://localhost:3000";

// ── route-gate mocks (for app/page.tsx) ──
vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: vi.fn(), usePathname: () => "/" }));
import RootPage from "../app/page";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}
async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

// ── fixtures ──
function spendFor(window: string) {
  return {
    window,
    bucket_size: window,
    totals: {
      bucket_start: "2026-06-01",
      bucket_end: "2026-06-15",
      requests: 1200,
      prompt_tokens: 90000,
      completion_tokens: 45000,
      cost_usd: "12.34",
    },
    buckets: [
      { bucket_start: "2026-06-13", requests: 100, prompt_tokens: 8000, completion_tokens: 4000, cost_usd: "1.00" },
      { bucket_start: "2026-06-14", requests: 150, prompt_tokens: 9000, completion_tokens: 4500, cost_usd: "1.50" },
    ],
    breakdown: null,
  };
}
const USAGE = {
  total_cost_usd: "12.34",
  total_requests: 1200,
  total_prompt_tokens: 90000,
  total_completion_tokens: 45000,
  records: [
    { id: "r1", model_id: "gpt-4o", prompt_tokens: 100, completion_tokens: 50, cost_usd: "0.01", status: 200, created_at: "2026-06-14T10:00:00Z" },
    { id: "r2", model_id: "claude-3", prompt_tokens: 200, completion_tokens: 80, cost_usd: "0.02", status: 200, created_at: "2026-06-14T09:00:00Z" },
  ],
};
const BUDGET = { budget_usd_monthly: "25.00", spent_usd_month: "10.50" };

let requested: string[] = [];
function mockMetrics(
  opts: { usage?: typeof USAGE; spend?: (w: string) => ReturnType<typeof spendFor> } = {},
) {
  const spend = opts.spend ?? spendFor;
  server.use(
    http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
      const w = new URL(request.url).searchParams.get("window") ?? "month";
      requested.push(`/admin/spend?window=${w}`);
      return HttpResponse.json(spend(w));
    }),
    http.get(`${APP}/api/gw/admin/usage`, () => {
      requested.push("/admin/usage");
      return HttpResponse.json(opts.usage ?? USAGE);
    }),
    http.get(`${APP}/api/gw/admin/budget`, () => {
      requested.push("/admin/budget");
      return HttpResponse.json(BUDGET);
    }),
  );
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => true,
    }),
  });
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});
afterEach(() => {
  requested = [];
  vi.mocked(cookies).mockReset();
  vi.mocked(redirect).mockReset();
});

// ── KPI CARDS ─────────────────────────────────────────────────────────────────
describe("Overview — KPI cards with trend deltas", () => {
  it("test_overview_renders_kpi_cards_with_deltas", async () => {
    mockMetrics();
    render(<OverviewPage />, { wrapper: Wrapper });
    for (const label of ["Total Requests", "Total Cost", "Total Tokens", "Monthly Spend"]) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    // EACH usage KPI carries a delta whose direction is in text/aria (sr-only word), not color alone.
    // Fixture: requests 100→150 (+50%), cost 1.00→1.50 (+50%), tokens 12000→13500 (+12.5%) → all "up".
    expect(screen.getAllByText("increase").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText("+50.0% vs prev").length).toBe(2); // Requests + Cost
    expect(screen.getByText("+12.5% vs prev")).toBeInTheDocument(); // Tokens
  });

  it("test_trend_neutral_when_no_prior_period", async () => {
    // a zero penultimate bucket has nothing to compare → contract degrades to neutral "—",
    // never a fabricated "+100%" (guards the prev===0 branch + the <2-bucket path)
    const zeroPrev = (window: string) => ({
      window,
      bucket_size: window,
      totals: { bucket_start: "2026-06-01", bucket_end: "2026-06-15", requests: 5, prompt_tokens: 10, completion_tokens: 5, cost_usd: "0.05" },
      buckets: [
        { bucket_start: "2026-06-13", requests: 0, prompt_tokens: 0, completion_tokens: 0, cost_usd: "0.00" },
        { bucket_start: "2026-06-14", requests: 5, prompt_tokens: 10, completion_tokens: 5, cost_usd: "0.05" },
      ],
      breakdown: null,
    });
    mockMetrics({ spend: zeroPrev });
    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    // neutral "—" delta for the usage KPIs (no fabricated +100%)
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText(/\+100% vs prev/)).toBeNull();
  });
});

// ── CHART ───────────────────────────────────────────────────────────────────
describe("Overview — usage-over-time chart", () => {
  it("test_chart_renders_from_buckets", async () => {
    mockMetrics();
    const { container } = render(<OverviewPage />, { wrapper: Wrapper });
    expect(await screen.findByText(/usage over time/i)).toBeInTheDocument();
    // ChartContainer injects a token-named css var (no raw hex in the consumer)
    await waitFor(() => expect(container.innerHTML).toContain("--color-"));
  });
});

// ── RANGE TOGGLE ──────────────────────────────────────────────────────────────
describe("Overview — range toggle refetches", () => {
  it("test_range_toggle_refetches", async () => {
    const user = userEvent.setup();
    mockMetrics();
    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    expect(requested.some((u) => u === "/admin/spend?window=month")).toBe(true);

    const dayBtn = screen.getByRole("button", { name: /^day$/i });
    await user.click(dayBtn);
    await waitFor(() => expect(requested.some((u) => u === "/admin/spend?window=day")).toBe(true));
    expect(dayBtn).toHaveAttribute("aria-pressed", "true");
  });
});

// ── ACTIVITY TABLE ────────────────────────────────────────────────────────────
describe("Overview — recent-activity table", () => {
  it("test_recent_activity_table_lists_records", async () => {
    mockMetrics();
    render(<OverviewPage />, { wrapper: Wrapper });
    expect(await screen.findByText("gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("claude-3")).toBeInTheDocument();
  });

  it("test_recent_activity_empty_state", async () => {
    mockMetrics({ usage: { ...USAGE, records: [] } });
    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    expect(await screen.findByText(/no recent activity/i)).toBeInTheDocument();
  });
});

// ── FOUR-STATE ────────────────────────────────────────────────────────────────
describe("Overview — four-state pattern", () => {
  it("test_four_state_loading_and_error", async () => {
    // loading: spend never resolves
    server.use(
      http.get(`${APP}/api/gw/admin/spend`, () => new Promise(() => {})),
      http.get(`${APP}/api/gw/admin/usage`, () => HttpResponse.json(USAGE)),
      http.get(`${APP}/api/gw/admin/budget`, () => HttpResponse.json(BUDGET)),
    );
    const { unmount } = render(<OverviewPage />, { wrapper: Wrapper });
    expect(await screen.findByRole("status")).toBeInTheDocument();
    unmount();

    // error: spend 500
    server.use(
      http.get(`${APP}/api/gw/admin/spend`, () => HttpResponse.json({ title: "boom" }, { status: 500 })),
      http.get(`${APP}/api/gw/admin/usage`, () => HttpResponse.json(USAGE)),
      http.get(`${APP}/api/gw/admin/budget`, () => HttpResponse.json(BUDGET)),
    );
    render(<OverviewPage />, { wrapper: Wrapper });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

// ── ROUTE GATE ────────────────────────────────────────────────────────────────
describe("Overview — / route auth gate", () => {
  it("test_root_redirects_unauthenticated", async () => {
    // mirror Next's real redirect (it THROWS to halt rendering) so the test proves the Overview
    // is NEVER rendered without a cookie — not merely that redirect() was called
    vi.mocked(cookies).mockResolvedValue({ get: () => undefined } as never);
    vi.mocked(redirect).mockImplementation((): never => {
      throw new Error("NEXT_REDIRECT");
    });
    await expect(RootPage()).rejects.toThrow("NEXT_REDIRECT");
    expect(redirect).toHaveBeenCalledWith("/login");
  });

  it("test_root_renders_overview_when_authenticated", async () => {
    vi.mocked(cookies).mockResolvedValue({
      get: (n: string) => ({ name: n, value: "x" }),
    } as never);
    const el = await RootPage();
    expect(redirect).not.toHaveBeenCalled();
    expect(el).toBeTruthy();
  });
});

// ── A11Y + DATA SEAM ──────────────────────────────────────────────────────────
describe("Overview — accessibility + data seam", () => {
  it("test_overview_axe_clean", async () => {
    mockMetrics();
    const { container } = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_data_seam_unchanged", async () => {
    mockMetrics();
    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("gpt-4o");
    // all THREE existing sources ARE called (no source dropped) ...
    await waitFor(() => {
      expect(requested).toContain("/admin/usage");
      expect(requested).toContain("/admin/budget");
      expect(requested.some((u) => u.startsWith("/admin/spend?window="))).toBe(true);
    });
    // ... and NOTHING ELSE (no new/renamed metrics route)
    const allowed = new Set(["/admin/usage", "/admin/budget"]);
    for (const u of requested) {
      const ok = allowed.has(u) || u.startsWith("/admin/spend?window=");
      expect(ok, `unexpected metrics request: ${u}`).toBe(true);
    }
  });
});
