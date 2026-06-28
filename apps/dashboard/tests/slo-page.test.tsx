/**
 * tests/slo-page.test.tsx — RED suite for the /app/slo SLO dashboard page.
 *
 * SloPage fetches GET /admin/slo?window_hours=N (via the BFF catch-all /api/gw/...)
 * and renders availability %, error rate %, total requests, and the success/client/server
 * breakdown for the selected window. Latency is shown as "not available yet" (the API
 * returns latency_ms: null — never fabricate a number).
 *
 * Contract (FROZEN § v1):
 *   - h1 "Service levels"
 *   - Window selector: 24h(24) / 7d(168) / 30d(720) → refetch GET /admin/slo?window_hours=
 *   - Cards: Availability (%), Error rate (%), Total requests, Breakdown (success/client/server)
 *   - Latency: "not available yet" placeholder (latency_ms is null)
 *   - Empty window: total 0 → 100% / 0 (no NaN)
 *   - A11Y: one h1, ordered headings, status conveyed by TEXT (not color alone), axe 0 serious/critical
 *
 * RED before Build: `@/components/slo/SloPage` does not exist yet → MODULE_NOT_FOUND,
 * the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { axe } from "@/test-support/axe";
import userEvent from "@testing-library/user-event";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { SloPage } from "@/components/slo/SloPage";

const APP = "http://localhost:3000";

// Nominal API response: 95% availability, 5% error, 100 total
const SLO_NOMINAL = {
  window_hours: 24,
  total_requests: 100,
  success_count: 90,
  client_error_count: 5,
  server_error_count: 5,
  availability: 0.95,
  error_rate: 0.05,
  latency_ms: null,
};

// Empty window: 0 requests, 100% availability (no NaN)
const SLO_EMPTY = {
  window_hours: 24,
  total_requests: 0,
  success_count: 0,
  client_error_count: 0,
  server_error_count: 0,
  availability: 1.0,
  error_rate: 0.0,
  latency_ms: null,
};

// 7d window response
const SLO_7D = {
  window_hours: 168,
  total_requests: 500,
  success_count: 480,
  client_error_count: 10,
  server_error_count: 10,
  availability: 0.96,
  error_rate: 0.04,
  latency_ms: null,
};

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

function renderSlo() {
  return render(<SloPage />, { wrapper: Wrapper });
}

/** Scope assertions to the SLO section landmark. */
function section() {
  return screen.getByRole("region", { name: /service levels/i });
}

describe("SloPage", () => {
  // test_slo_renders_metrics: renders availability/error-rate/total/breakdown from mock
  it("test_slo_renders_metrics", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/slo`, () => HttpResponse.json(SLO_NOMINAL)),
    );

    renderSlo();

    await waitFor(() => {
      // availability rendered as text % — "95%" (exact testid)
      expect(within(section()).getByTestId("slo-availability")).toHaveTextContent("95%");
    });

    const sec = section();
    // error rate rendered as text % — "5%" (exact testid)
    expect(within(sec).getByTestId("slo-error-rate")).toHaveTextContent("5%");
    // total requests
    expect(within(sec).getByTestId("slo-total-requests")).toHaveTextContent("100");
    // breakdown: success count
    expect(within(sec).getByText(/^90$/)).toBeInTheDocument();
    // breakdown: client and server errors present (multiple "5" values OK)
    expect(within(sec).getAllByText(/^5$/).length).toBeGreaterThan(0);
  });

  // test_slo_window_selector: picking 7d refetches window_hours=168
  it("test_slo_window_selector", async () => {
    const fetchedUrls: string[] = [];

    server.use(
      http.get(`${APP}/api/gw/admin/slo`, ({ request }) => {
        fetchedUrls.push(request.url);
        return HttpResponse.json(SLO_7D);
      }),
    );

    renderSlo();

    // Wait for initial load (24h default)
    await waitFor(() => {
      expect(fetchedUrls.length).toBeGreaterThan(0);
    });

    // Pick the 7d option
    const selector = section().querySelector("select, [role='combobox'], button[aria-haspopup]");
    // Find a button or select containing "7d" text and click it
    const seventDayOption = screen.getByRole("button", { name: /7.?d/i }) ??
      screen.queryByText(/7.?day/i);

    // Fallback: look for an element with text matching 7d
    const all7d = screen.getAllByText(/7d/i);
    if (all7d.length > 0) {
      fireEvent.click(all7d[0]);
    }

    await waitFor(() => {
      expect(fetchedUrls.some((u) => u.includes("window_hours=168"))).toBe(true);
    });
  });

  // test_slo_latency_placeholder: latency_ms null → "not available yet"
  it("test_slo_latency_placeholder", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/slo`, () => HttpResponse.json(SLO_NOMINAL)),
    );

    renderSlo();

    await user.click(screen.getByRole("tab", { name: /latency/i }));
    await waitFor(() => {
      expect(within(section()).getByText(/not available yet/i)).toBeInTheDocument();
    });

    // Must never show a fabricated number for latency
    expect(within(section()).queryByText(/\d+\s*ms/)).not.toBeInTheDocument();
  });

  // test_slo_empty_window: total 0 / avail 1.0 → 100% / 0 (no NaN)
  it("test_slo_empty_window", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/slo`, () => HttpResponse.json(SLO_EMPTY)),
    );

    renderSlo();

    await waitFor(() => {
      expect(within(section()).getByTestId("slo-availability")).toHaveTextContent("100%");
    });

    const sec = section();
    // Total requests: 0 (via testid — unambiguous)
    expect(within(sec).getByTestId("slo-total-requests")).toHaveTextContent("0");
    // No NaN anywhere in the section
    expect(within(sec).queryByText(/NaN/)).not.toBeInTheDocument();
    // Error rate: 0% (via testid)
    expect(within(sec).getByTestId("slo-error-rate")).toHaveTextContent("0%");
  });

  // test_slo_a11y: axe 0 serious/critical; one h1; status text-labelled (not color-only)
  it("test_slo_a11y", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/slo`, () => HttpResponse.json(SLO_NOMINAL)),
    );

    const { container } = renderSlo();

    await waitFor(() => {
      expect(within(section()).getByText(/95%/)).toBeInTheDocument();
    });

    // Exactly one h1
    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);

    // Status conveyed by text (%), not color alone — the text "95%" must be present as text
    expect(container.querySelector("h1")!.textContent).toMatch(/service levels/i);

    // axe: 0 serious/critical violations (disable color-contrast — jsdom has no canvas)
    const results = await axe(container, {
      rules: { "color-contrast": { enabled: false } },
    });
    const seriousCritical = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(seriousCritical).toEqual([]);
  });

  // test_slo_loading_state: shows a loading indicator before data arrives
  it("test_slo_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/slo`,
        () => new Promise<never>(() => { /* never resolves */ }),
      ),
    );

    renderSlo();

    await waitFor(() => {
      const spinner =
        within(section()).queryByRole("status") ??
        document.querySelector("[aria-busy='true']") ??
        document.querySelector(".animate-pulse, .animate-spin");
      expect(spinner).toBeTruthy();
    });
  });

  // test_slo_error_state: renders error when API fails
  it("test_slo_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/slo`, () =>
        HttpResponse.json(
          { title: "Internal server error", status: 500 },
          { status: 500 },
        ),
      ),
    );

    renderSlo();

    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
  });
});
