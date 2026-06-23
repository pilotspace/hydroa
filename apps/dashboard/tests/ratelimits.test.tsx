/**
 * tests/ratelimits.test.tsx — RED suite for the ratelimit-counter-view dashboard panel.
 *
 * RatelimitsPanel fetches GET /admin/ratelimits (via the BFF catch-all /api/gw/...) and renders
 * each key's live rpm/tpm consumption against its configured limit. It must handle the four
 * states (success / empty / error / loading) like every other dashboard surface, render null
 * counters as "—" (unknown — Redis down), and null limits as "∞".
 *
 * RED before Build: `@/components/keys/RatelimitsPanel` does not exist yet → MODULE_NOT_FOUND,
 * the established true-red convention. Frozen contract (§3): response is { keys:[...] }.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { RatelimitsPanel } from "@/components/keys/RatelimitsPanel";

const APP = "http://localhost:3000";

const RL_RESPONSE = {
  keys: [
    {
      key_id: "00000000-0000-0000-0000-000000000001",
      name: "prod-key",
      rpm_limit: 60,
      tpm_limit: 1000,
      rpm_current: 3,
      tpm_current: 150.0,
    },
  ],
};

const RL_NULL_COUNTERS = {
  keys: [
    {
      key_id: "00000000-0000-0000-0000-000000000002",
      name: "redis-down-key",
      rpm_limit: 60,
      tpm_limit: null,
      rpm_current: null,
      tpm_current: null,
    },
  ],
};

const RL_EMPTY = { keys: [] };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderPanel() {
  return render(<RatelimitsPanel />, { wrapper: Wrapper });
}

function section() {
  return screen.getByRole("region", { name: /rate-limit/i });
}

describe("RatelimitsPanel", () => {
  it("test_ratelimits_renders_current_vs_limit", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/ratelimits`, () => HttpResponse.json(RL_RESPONSE)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/prod-key/i)).toBeInTheDocument();
    });
    // current / limit shown for rpm (3 / 60) and tpm (150 / 1000)
    expect(within(section()).getByText(/3 \/ 60/)).toBeInTheDocument();
    expect(within(section()).getByText(/150 \/ 1000/)).toBeInTheDocument();
  });

  it("test_ratelimits_null_counter_renders_unknown", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/ratelimits`, () => HttpResponse.json(RL_NULL_COUNTERS)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/redis-down-key/i)).toBeInTheDocument();
    });
    // null counter -> "—" (unknown), null limit -> "∞"; never falsely "0"
    expect(within(section()).getByText(/— \/ 60/)).toBeInTheDocument();
    expect(within(section()).getByText(/— \/ ∞/)).toBeInTheDocument();
  });

  it("test_ratelimits_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/ratelimits`, () => HttpResponse.json(RL_EMPTY)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/no keys/i)).toBeInTheDocument();
    });
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_ratelimits_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/ratelimits`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_ratelimits_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/ratelimits`,
        () => new Promise<never>(() => { /* never resolves */ }),
      ),
    );

    renderPanel();

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
