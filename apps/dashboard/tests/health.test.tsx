/**
 * tests/health.test.tsx — RED suite for the upstream-health-view dashboard page.
 *
 * HealthPage fetches GET /admin/health/upstreams (via the BFF catch-all /api/gw/...) and renders
 * the up/down status of each MONITORED upstream in a table: Upstream, Status, Last event. It must
 * handle the four states (success / empty / error / loading) like every other dashboard page and
 * must NOT fabricate rows for unpinged providers.
 *
 * RED before Build: `@/components/health/HealthPage` does not exist yet → MODULE_NOT_FOUND, the
 * established true-red convention. Frozen contract (§3): response is
 * { checked_at:str, upstreams:[{name, status, last_event_at, last_event_type}] }.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { HealthPage } from "@/components/health/HealthPage";

const APP = "http://localhost:3000";

const HEALTH_UP = {
  checked_at: "2026-06-22T12:00:00Z",
  upstreams: [
    {
      name: "openrouter",
      status: "up",
      last_event_at: "2026-06-10T12:05:00Z",
      last_event_type: "upstream_health_recovered",
    },
  ],
};

const HEALTH_DOWN = {
  checked_at: "2026-06-22T12:00:00Z",
  upstreams: [
    {
      name: "openrouter",
      status: "down",
      last_event_at: "2026-06-10T12:05:00Z",
      last_event_type: "upstream_health_fail",
    },
  ],
};

const HEALTH_EMPTY = { checked_at: "2026-06-22T12:00:00Z", upstreams: [] };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderHealth() {
  return render(<HealthPage />, { wrapper: Wrapper });
}

/** Scope assertions to the health section (CONVENTIONS.md within(<section>) rule). */
function section() {
  return screen.getByRole("region", { name: /health/i });
}

describe("HealthPage", () => {
  it("test_health_renders_up_row", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/health/upstreams`, () => HttpResponse.json(HEALTH_UP)),
    );

    renderHealth();

    await waitFor(() => {
      expect(within(section()).getByText(/openrouter/i)).toBeInTheDocument();
    });
    expect(within(section()).getByText(/^up$/i)).toBeInTheDocument();
    // honesty: only the monitored upstream — no fabricated provider rows
    expect(within(section()).queryByText(/anthropic|gemini|bedrock|azure/i)).not.toBeInTheDocument();
  });

  it("test_health_renders_down_row", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/health/upstreams`, () => HttpResponse.json(HEALTH_DOWN)),
    );

    renderHealth();

    await waitFor(() => {
      expect(within(section()).getByText(/openrouter/i)).toBeInTheDocument();
    });
    expect(within(section()).getByText(/^down$/i)).toBeInTheDocument();
  });

  it("test_health_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/health/upstreams`, () => HttpResponse.json(HEALTH_EMPTY)),
    );

    renderHealth();

    await waitFor(() => {
      expect(within(section()).getByText(/no upstreams/i)).toBeInTheDocument();
    });
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_health_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/health/upstreams`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderHealth();

    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_health_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/health/upstreams`,
        () => new Promise<never>(() => { /* never resolves */ }),
      ),
    );

    renderHealth();

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
