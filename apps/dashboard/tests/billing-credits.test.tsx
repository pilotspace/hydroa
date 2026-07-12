/**
 * tests/billing-credits.test.tsx — RED suite for the Credits page
 * (billing-ui TASK.md §3 — FROZEN @ v1, M6, R5, M11).
 *
 * RED before Build: `@/components/credits/CreditsPage` does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import React from "react";

import { CreditsPage } from "@/components/credits/CreditsPage";

const APP = "http://localhost:3000";
const BALANCE_URL = `${APP}/api/gw/admin/credits/balance`;
const HISTORY_URL = `${APP}/api/gw/admin/credits/history`;

const BALANCE_WITH_GRACE = { tenant_id: "t1", balance_usd: "42.50", grace_usd: "5.00", updated_at: "2026-07-10T00:00:00Z" };
const BALANCE_ZERO = { tenant_id: "t1", balance_usd: "0.00", grace_usd: "0.00", updated_at: "2026-07-10T00:00:00Z" };

const HISTORY_ENTRIES = {
  entries: [
    { id: "e1", entry_type: "topup", amount_usd: "50.00", balance_after_usd: "92.50", reference_type: null, reference_id: null, request_id: null, created_at: "2026-07-10T14:02:00Z" },
    { id: "e2", entry_type: "hold", amount_usd: "-0.50", balance_after_usd: "42.50", reference_type: null, reference_id: null, request_id: "req-1", created_at: "2026-07-10T09:11:00Z" },
    { id: "e3", entry_type: "settle", amount_usd: "0.29", balance_after_usd: "42.79", reference_type: "usage_record", reference_id: "ur-1", request_id: "req-1", created_at: "2026-07-10T09:11:00Z" },
  ],
  next_cursor: null,
};
const HISTORY_EMPTY = { entries: [], next_cursor: null };

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}
function renderCredits() {
  return render(<CreditsPage />, { wrapper: Wrapper });
}
function section() {
  return screen.getByRole("region");
}

describe("CreditsPage — hero balance + history (M6)", () => {
  it("test_hero_balance_and_paginated_history_render", async () => {
    server.use(
      http.get(BALANCE_URL, () => HttpResponse.json(BALANCE_WITH_GRACE)),
      http.get(HISTORY_URL, () => HttpResponse.json(HISTORY_ENTRIES)),
    );
    renderCredits();

    await waitFor(() => expect(screen.getByTestId("credits-balance-value")).toHaveTextContent("$42.50"));
    expect(within(section()).getByText(/\+\s?\$5\.00 grace/i)).toBeInTheDocument();
    expect(within(section()).getByText(/topup/i)).toBeInTheDocument();
    expect(within(section()).getByText("$92.50")).toBeInTheDocument();
  });

  it("test_brand_new_tenant_zero_activity_sees_informative_empty_state_nav_still_visible", async () => {
    server.use(
      http.get(BALANCE_URL, () => HttpResponse.json(BALANCE_ZERO)),
      http.get(HISTORY_URL, () => HttpResponse.json(HISTORY_EMPTY)),
    );
    renderCredits();

    await waitFor(() => expect(within(section()).getByText(/no credit activity yet/i)).toBeInTheDocument());
    expect(within(section()).getByText(/contact them to add credits/i)).toBeInTheDocument();
  });

  it("test_platform_wide_kill_switch_off_renders_the_same_honest_empty_state", async () => {
    // No API signal distinguishes "unused" from "kill-switch off" (R5) — same fixture,
    // same assertion as the brand-new-tenant case; the UI never claims/implies "disabled".
    server.use(
      http.get(BALANCE_URL, () => HttpResponse.json(BALANCE_ZERO)),
      http.get(HISTORY_URL, () => HttpResponse.json(HISTORY_EMPTY)),
    );
    renderCredits();

    await waitFor(() => expect(within(section()).getByText(/no credit activity yet/i)).toBeInTheDocument());
    expect(within(section()).queryByText(/disabled/i)).not.toBeInTheDocument();
  });

  it("test_axe_no_serious_violations", async () => {
    server.use(
      http.get(BALANCE_URL, () => HttpResponse.json(BALANCE_WITH_GRACE)),
      http.get(HISTORY_URL, () => HttpResponse.json(HISTORY_ENTRIES)),
    );
    const { container } = renderCredits();
    await waitFor(() => expect(screen.getByTestId("credits-balance-value")).toHaveTextContent("$42.50"));
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});
