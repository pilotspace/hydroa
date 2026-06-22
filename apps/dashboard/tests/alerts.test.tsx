/**
 * tests/alerts.test.tsx — RED suite for the alerts-events-viewer dashboard page.
 *
 * AlertsPage fetches GET /admin/alerts (via the BFF catch-all /api/gw/admin/alerts) and renders
 * the tenant-visible alert history in a table: Type, When, Status (delivered vs pending). It must
 * handle the four states (success / empty / error / loading) like every other dashboard page.
 *
 * RED before Build: `@/components/alerts/AlertsPage` does not exist yet → MODULE_NOT_FOUND, the
 * established true-red convention. Frozen contract (§3): response is { items:[...], total:int }.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { AlertsPage } from "@/components/alerts/AlertsPage";

const APP = "http://localhost:3000";

const ALERTS_RESPONSE = {
  items: [
    {
      id: "00000000-0000-0000-0000-000000000002",
      event_type: "circuit_breaker_open",
      payload: { provider: "anthropic" },
      created_at: "2026-06-10T12:05:00Z",
      delivered: false,
      delivered_at: null,
    },
    {
      id: "00000000-0000-0000-0000-000000000001",
      event_type: "soft_budget_exceeded",
      payload: { soft_budget_usd: "25.00", key_spend_usd: "26.10" },
      created_at: "2026-06-10T12:00:00Z",
      delivered: true,
      delivered_at: "2026-06-10T12:01:00Z",
    },
  ],
  total: 2,
};

const ALERTS_EMPTY_RESPONSE = { items: [], total: 0 };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderAlerts() {
  return render(<AlertsPage />, { wrapper: Wrapper });
}

/** Scope assertions to the alerts section (CONVENTIONS.md within(<section>) rule). */
function section() {
  return screen.getByRole("region", { name: /alert/i });
}

describe("AlertsPage", () => {
  it("test_alerts_renders_history_table", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/alerts`, () => HttpResponse.json(ALERTS_RESPONSE)),
    );

    renderAlerts();

    await waitFor(() => {
      expect(within(section()).getByText(/circuit_breaker_open/i)).toBeInTheDocument();
    });
    const sec = section();
    // both rows present (tenant soft-budget + platform system event)
    expect(within(sec).getByText(/soft_budget_exceeded/i)).toBeInTheDocument();
    // delivered status: one delivered, one pending
    expect(within(sec).getByText(/delivered/i)).toBeInTheDocument();
    expect(within(sec).getByText(/pending/i)).toBeInTheDocument();
  });

  it("test_alerts_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/alerts`, () => HttpResponse.json(ALERTS_EMPTY_RESPONSE)),
    );

    renderAlerts();

    await waitFor(() => {
      expect(within(section()).getByText(/no alerts yet/i)).toBeInTheDocument();
    });
    // empty is not an error
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_alerts_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/alerts`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderAlerts();

    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
    // no data rows on error
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_alerts_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/alerts`,
        () => new Promise<never>(() => { /* never resolves */ }),
      ),
    );

    renderAlerts();

    await waitFor(() => {
      const spinner =
        within(section()).queryByRole("status") ??
        document.querySelector("[aria-busy='true']") ??
        document.querySelector(".animate-pulse, .animate-spin");
      expect(spinner).toBeTruthy();
    });
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });
});
