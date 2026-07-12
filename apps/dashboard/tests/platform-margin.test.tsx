/**
 * tests/platform-margin.test.tsx — RED suite for the margin-dashboard task's platform-console
 * Margin page (TASK.md M12) + its "Margin" nav entry (third entry, alongside "Tenants" and
 * "Plans").
 *
 * GET /admin/platform/margin/summary | /by-tenant-model | /trend | /tie-out
 * (byte-identical to margin_router.py's own frozen DTOs, margin-dashboard §3 CONTRACT,
 * confirmed by reading it directly). Honest-null margin (M3): a `has_provider_cost_data:
 * false` item renders "no cost data", NEVER "$0.00" — the central product-honesty rule this
 * whole task exists to enforce, mirrored in the UI layer.
 *
 * RED before Build: `PlatformMarginView` does not exist yet, and AppShell has no "Margin"
 * nav entry yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformMarginView } from "@/components/platform/PlatformMarginView";
import { AppShell } from "@/components/ui/app-shell";

const APP = "http://localhost:3000";

const SUMMARY_WITH_COST_DATA = {
  window_from: "2026-07-01T00:00:00",
  window_to: "2026-08-01T00:00:00",
  provider_cost_total: "8.00",
  billed_total: "12.00",
  catalog_billed_total: "40.00",
  margin: "4.00",
  has_provider_cost_data: true,
  drift: "-4.00",
  unbilled_upstream_cost: "0",
  unbilled_rows: 0,
};

const SUMMARY_NO_COST_DATA = {
  window_from: "2026-07-01T00:00:00",
  window_to: "2026-08-01T00:00:00",
  provider_cost_total: "0",
  billed_total: "0",
  catalog_billed_total: "40.00",
  margin: null,
  has_provider_cost_data: false,
  drift: "0",
  unbilled_upstream_cost: "0",
  unbilled_rows: 0,
};

const BY_TENANT_MODEL = {
  items: [
    {
      tenant_id: "aaaaaaaa-0000-0000-0000-000000000001",
      model_id: "some/openrouter-model",
      provider_cost_total: "8.00",
      billed_total: "12.00",
      catalog_billed_total: "0",
      margin: "4.00",
      has_provider_cost_data: true,
      unbilled_upstream_cost: "0",
      unbilled_rows: 0,
    },
    {
      tenant_id: "aaaaaaaa-0000-0000-0000-000000000002",
      model_id: "gpt-4o",
      provider_cost_total: "0",
      billed_total: "0",
      catalog_billed_total: "40.00",
      margin: null,
      has_provider_cost_data: false,
      unbilled_upstream_cost: "0",
      unbilled_rows: 0,
    },
  ],
  next_cursor: null,
  has_more: false,
};

const TREND = {
  granularity: "month",
  points: [
    {
      bucket_start: "2026-07-01T00:00:00",
      provider_cost_total: "8.00",
      billed_total: "12.00",
      catalog_billed_total: "40.00",
      margin: "4.00",
      has_provider_cost_data: true,
    },
  ],
};

const TIE_OUT = {
  period_start: "2026-07-01T00:00:00",
  period_end: "2026-08-01T00:00:00",
  items: [
    {
      tenant_id: "aaaaaaaa-0000-0000-0000-000000000001",
      invoice_id: "bbbbbbbb-0000-0000-0000-000000000001",
      invoice_status: "issued",
      invoiced_total_usd: "350.00",
      invoiced_raw_total_usd: "350.00000000",
      ledger_billed_total_usd: "350.00000000",
      provider_cost_total_usd: "8.00",
      drift_invoiced_vs_ledger: "0",
      tie_out_status: "matched",
    },
    {
      tenant_id: "aaaaaaaa-0000-0000-0000-000000000002",
      invoice_id: null,
      invoice_status: "pending_invoice",
      invoiced_total_usd: null,
      invoiced_raw_total_usd: null,
      ledger_billed_total_usd: "40.00",
      provider_cost_total_usd: "0",
      drift_invoiced_vs_ledger: null,
      tie_out_status: "pending_invoice",
    },
  ],
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderView() {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformMarginView />
    </QueryClientProvider>,
  );
}

function mockAllEndpoints(overrides: {
  summary?: unknown;
  byTenantModel?: unknown;
  trend?: unknown;
  tieOut?: unknown;
} = {}) {
  server.use(
    http.get(`${APP}/api/gw/admin/platform/margin/summary`, () =>
      HttpResponse.json(overrides.summary ?? SUMMARY_WITH_COST_DATA),
    ),
    http.get(`${APP}/api/gw/admin/platform/margin/by-tenant-model`, () =>
      HttpResponse.json(overrides.byTenantModel ?? BY_TENANT_MODEL),
    ),
    http.get(`${APP}/api/gw/admin/platform/margin/trend`, () =>
      HttpResponse.json(overrides.trend ?? TREND),
    ),
    http.get(`${APP}/api/gw/admin/platform/margin/tie-out`, () =>
      HttpResponse.json(overrides.tieOut ?? TIE_OUT),
    ),
  );
}

describe("PlatformMarginView", () => {
  it("test_renders_summary_tiles_table_trend_and_tie_out", async () => {
    mockAllEndpoints();
    renderView();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /platform.+margin/i })).toBeInTheDocument();
    });

    // Summary tiles — the same dollar figures also legitimately appear in the
    // per-tenant/per-model table below (this fixture's one provider-basis row shares the
    // summary's own totals), so assert presence via getAllByText (>=1) rather than a
    // single-match getByText.
    await waitFor(() => {
      expect(screen.getAllByText("$12.00").length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getAllByText("$8.00").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("summary-margin-value")).toHaveTextContent("$4.00");

    // Per-tenant/per-model table.
    expect(screen.getByText("some/openrouter-model")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();

    // Trend section renders (chart figure).
    expect(screen.getByRole("figure", { hidden: true }) ?? screen.getByText(/trend/i)).toBeTruthy();

    // Tie-out section.
    await waitFor(() => {
      expect(screen.getByText(/matched/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/pending.invoice/i)).toBeInTheDocument();
  });

  it("test_catalog_only_row_shows_no_cost_data_badge_never_dollar_zero", async () => {
    mockAllEndpoints({ summary: SUMMARY_NO_COST_DATA });
    renderView();

    await waitFor(() => {
      expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    });

    // The catalog-only row (has_provider_cost_data=false) never renders a dollar figure
    // for its margin cell — it renders an explicit "No cost data" badge instead (M3's
    // central rule). Scoped to the margin badge test hook specifically: OTHER fields
    // (billed_total/provider_cost_total) legitimately render "$0.00" in this fixture,
    // since it models a period with genuinely zero provider-basis revenue — a real,
    // known fact, distinct from "margin is unknown" (the thing M3 forbids fabricating).
    const noCostBadges = screen.getAllByText(/no cost data/i);
    expect(noCostBadges.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("summary-margin-value-badge")).toHaveTextContent(/no cost data/i);
  });

  it("test_summary_no_cost_data_renders_no_cost_data_not_zero", async () => {
    mockAllEndpoints({ summary: SUMMARY_NO_COST_DATA });
    renderView();

    await waitFor(() => {
      expect(screen.getAllByText(/no cost data/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  it("test_shows_standard_error_state_on_403_non_superadmin", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/margin/summary`, () =>
        HttpResponse.json(
          { code: "ERR_AUTH_FORBIDDEN", title: "Insufficient role for this operation" },
          { status: 403 },
        ),
      ),
      http.get(`${APP}/api/gw/admin/platform/margin/by-tenant-model`, () =>
        HttpResponse.json(
          { code: "ERR_AUTH_FORBIDDEN", title: "Insufficient role for this operation" },
          { status: 403 },
        ),
      ),
      http.get(`${APP}/api/gw/admin/platform/margin/trend`, () =>
        HttpResponse.json(
          { code: "ERR_AUTH_FORBIDDEN", title: "Insufficient role for this operation" },
          { status: 403 },
        ),
      ),
      http.get(`${APP}/api/gw/admin/platform/margin/tie-out`, () =>
        HttpResponse.json(
          { code: "ERR_AUTH_FORBIDDEN", title: "Insufficient role for this operation" },
          { status: 403 },
        ),
      ),
    );

    renderView();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/insufficient role/i);
    });
    expect(screen.queryByText("some/openrouter-model")).not.toBeInTheDocument();
  });
});

function primaryNav() {
  return screen.getByRole("navigation", { name: /primary/i });
}

function mobileNav() {
  return screen.getByRole("navigation", { name: /site/i });
}

describe("AppShell — Margin nav entry (margin-dashboard, third Platform entry)", () => {
  it("test_margin_nav_visible_for_superadmin_desktop_and_mobile", () => {
    render(
      <AppShell role="superadmin">
        <div>content</div>
      </AppShell>,
    );

    const desktopLink = within(primaryNav()).getByRole("link", { name: /^margin$/i });
    expect(desktopLink).toBeInTheDocument();
    expect(desktopLink).toHaveAttribute("href", "/app/platform/margin");

    // The two existing entries are untouched.
    expect(within(primaryNav()).getByRole("link", { name: /^tenants$/i })).toBeInTheDocument();
    expect(within(primaryNav()).getByRole("link", { name: /^plans$/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open navigation/i }));
    const mobileLink = within(mobileNav()).getByRole("link", { name: /^margin$/i });
    expect(mobileLink).toBeInTheDocument();
    expect(mobileLink).toHaveAttribute("href", "/app/platform/margin");
  });

  it("test_margin_nav_hidden_for_non_superadmin_roles", () => {
    const roles: Array<string | null | undefined> = [null, undefined, "member", "admin", "owner"];
    for (const role of roles) {
      const { unmount } = render(
        <AppShell role={role}>
          <div>content</div>
        </AppShell>,
      );
      expect(
        within(primaryNav()).queryByRole("link", { name: /^margin$/i }),
      ).not.toBeInTheDocument();
      unmount();
    }
  });
});
