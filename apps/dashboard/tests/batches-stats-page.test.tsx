/**
 * tests/batches-stats-page.test.tsx — RED suite for batch-dashboard-surface (v57).
 *
 * BatchesStatsPage — read-only admin statistics page (savings + volume + status
 * breakdown). Mirrors AuditPage.tsx's minimal 3-state shape (no Tabs — a single
 * GET /admin/batches/stats query), plus an app-level Empty check on top of the
 * 4-state pattern (total_requests === 0 renders Empty ALONGSIDE the honest-zero
 * StatCards, per TASK.md §2's "Empty tenant sees the Empty state" scenario —
 * distinct from the query itself failing).
 *
 * RED before Build: `@/components/batches/BatchesStatsPage` does not exist yet →
 * MODULE_NOT_FOUND, the established true-red convention (see usage.test.tsx).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── RED: this import fails until Build creates components/batches/BatchesStatsPage.tsx ──
import { BatchesStatsPage } from "@/components/batches/BatchesStatsPage";

const STATS_URL = "http://localhost:3000/api/gw/admin/batches/stats";

const STATS_ZERO = {
  savings_usd: "0.00",
  total_requests: 0,
  status_counts: { pending: 0, succeeded: 0, errored: 0, canceled: 0, expired: 0 },
};

const STATS_ACTIVE = {
  savings_usd: "0.00",
  total_requests: 12,
  status_counts: { pending: 3, succeeded: 7, errored: 1, canceled: 1, expired: 0 },
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderPage() {
  return render(<BatchesStatsPage />, { wrapper: Wrapper });
}

describe("BatchesStatsPage", () => {
  /**
   * Scenario: Admin views the batches stats page   # M1
   */
  it("test_admin_views_batches_stats_page", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ACTIVE)));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("$0.00")).toBeInTheDocument();
    });
    // no composer/submit UI anywhere on this read-only page
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit|new batch|create/i })).not.toBeInTheDocument();
  });

  /**
   * Scenario: Savings StatCard shows the honest current value   # M2
   */
  it("test_savings_shows_honest_current_value", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ACTIVE)));

    renderPage();

    // savings stays "$0.00" even while other stats are non-zero — a constant,
    // not derived from total_requests/status_counts.
    await waitFor(() => {
      expect(screen.getByText("$0.00")).toBeInTheDocument();
    });
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  /**
   * Scenario: Volume and status-breakdown StatCards show real, currently-zero numbers   # M3, M4, M5
   */
  it("test_volume_and_status_breakdown_show_real_zero_numbers", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ZERO)));

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(4);
    });
  });

  /**
   * Scenario: Page reflects real batch activity once it exists   # M3, M4, M5, A1
   */
  it("test_page_reflects_real_batch_activity", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ACTIVE)));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("12")).toBeInTheDocument(); // total requests
    });
    expect(screen.getByText("7")).toBeInTheDocument(); // succeeded
    expect(screen.getByText("1")).toBeInTheDocument(); // errored (canceled also =1, fine to share)
    expect(screen.getByText("3")).toBeInTheDocument(); // pending -> "In progress"
    // real activity -> the Empty block must NOT render
    expect(screen.queryByText(/no batch activity yet/i)).not.toBeInTheDocument();
  });

  /**
   * Scenario: Empty tenant sees the Empty state, not an error   # M6, R2
   */
  it("test_empty_tenant_sees_empty_state_not_error", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ZERO)));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/no batch activity yet/i)).toBeInTheDocument();
    });
    // honest-zero StatCards stay visible ALONGSIDE the Empty block, never hidden by it
    expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  /**
   * Scenario: A query failure shows ErrorState, not a silent zero   # M6, R2
   */
  it("test_query_failure_shows_error_state_not_silent_zero", async () => {
    server.use(
      http.get(STATS_URL, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
    // never render zeros as if they were a real (possibly-misleading) answer
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText(/no batch activity yet/i)).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------
  // batch-observability-scaffolding: 3 new StatCards (window-wait latency +
  // 2 honest placeholders pending v58). STATS_ZERO/STATS_ACTIVE above are the
  // PRE-EXISTING mocks and deliberately omit the 3 new fields entirely —
  // proving the page tolerates absence exactly like an explicit null.
  // ---------------------------------------------------------------------

  /**
   * Scenario: the 3 new StatCards render even against old mocks lacking the new fields
   */
  it("test_all_three_new_statcards_render_with_default_mocks", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ACTIVE)));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Avg. queue wait")).toBeInTheDocument();
    });
    expect(screen.getByText("Avg. completion latency")).toBeInTheDocument();
    expect(screen.getByText("Token throughput (TPM)")).toBeInTheDocument();
  });

  /**
   * Scenario: Avg. queue wait shows a real value once windows have flushed
   */
  it("test_avg_window_wait_shows_real_value_when_present", async () => {
    server.use(
      http.get(STATS_URL, () =>
        HttpResponse.json({
          ...STATS_ACTIVE,
          avg_window_wait_ms: 3750.4,
          avg_completion_latency_ms: null,
          completion_tpm: null,
        }),
      ),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("3750 ms")).toBeInTheDocument();
    });
  });

  /**
   * Scenario: Avg. queue wait shows an honest empty state before any window has flushed
   */
  it("test_avg_window_wait_shows_dash_and_footer_when_no_samples_yet", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ZERO)));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("No windows have flushed yet.")).toBeInTheDocument();
    });
  });

  /**
   * Scenario: the completion-latency/TPM placeholders read distinctly from the
   * window-wait card's own empty-state caption — both can show a dash today, for
   * two DIFFERENT reasons, and must not read as the same bug (TASK.md §1 Must).
   */
  it("test_completion_placeholders_read_distinctly_from_window_wait_empty_state", async () => {
    server.use(
      http.get(STATS_URL, () =>
        HttpResponse.json({
          ...STATS_ACTIVE,
          avg_window_wait_ms: 3750.4,
          avg_completion_latency_ms: null,
          completion_tpm: null,
        }),
      ),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("3750 ms")).toBeInTheDocument();
    });
    // window-wait has real data now -> its OWN empty-state footer must be gone
    expect(screen.queryByText("No windows have flushed yet.")).not.toBeInTheDocument();
    // the two placeholder cards are STILL "—", for a genuinely different reason,
    // worded distinctly so an admin doesn't mistake this for the same bug
    expect(screen.getAllByText("—").length).toBe(2);
    expect(
      screen.getAllByText("Lands once real batch processing exists (v58).").length,
    ).toBe(2);
  });

  /**
   * Scenario: the page carries a visible experimental notice, regardless of data state —
   * admins must know this whole feature (and its still-placeholder metrics) is
   * experimental before they read too much into any number on it.
   */
  it("test_experimental_notice_always_visible", async () => {
    server.use(http.get(STATS_URL, () => HttpResponse.json(STATS_ACTIVE)));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Experimental")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/batch processing is still being rolled out/i),
    ).toBeInTheDocument();
  });
});
