/**
 * tests/bandwidth.test.tsx — RED suite for the bandwidth-panel dashboard view (v37).
 *
 * BandwidthPanel fetches GET /admin/bandwidth (via the BFF catch-all /api/gw/...) and renders each
 * key's refill-adjusted bucket level against capacity (burst). Like every dashboard surface it
 * handles the four states (success / empty / error / loading), renders a null level as "—" (unknown
 * — Redis down / untouched / disabled), NEVER "0", and shows a capacity caption ("{rate} tokens/sec
 * · burst {burst}" when enabled, "Pacing disabled" when off).
 *
 * RED before Build: `@/components/keys/BandwidthPanel` does not exist yet → MODULE_NOT_FOUND, the
 * established true-red convention. Frozen contract (§3 v1): response is
 * { enabled, rate_per_sec, burst, keys:[{key_id, name, level:number|null}] }.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { BandwidthPanel } from "@/components/keys/BandwidthPanel";

const APP = "http://localhost:3000";

const BW_RESPONSE = {
  enabled: true,
  rate_per_sec: 100,
  burst: 200,
  keys: [
    { key_id: "00000000-0000-0000-0000-000000000001", name: "prod-key", level: 150 },
  ],
};

const BW_NULL_LEVEL = {
  enabled: true,
  rate_per_sec: 100,
  burst: 200,
  keys: [
    { key_id: "00000000-0000-0000-0000-000000000002", name: "quiet-key", level: null },
  ],
};

const BW_DISABLED = {
  enabled: false,
  rate_per_sec: 0,
  burst: 0,
  keys: [
    { key_id: "00000000-0000-0000-0000-000000000003", name: "k", level: null },
  ],
};

const BW_EMPTY = { enabled: true, rate_per_sec: 100, burst: 200, keys: [] };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderPanel() {
  return render(<BandwidthPanel />, { wrapper: Wrapper });
}

function section() {
  return screen.getByRole("region", { name: /bandwidth/i });
}

describe("BandwidthPanel", () => {
  it("test_bandwidth_renders_level_vs_capacity", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/bandwidth`, () => HttpResponse.json(BW_RESPONSE)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/prod-key/i)).toBeInTheDocument();
    });
    // level / capacity (burst)
    expect(within(section()).getByText(/150 \/ 200/)).toBeInTheDocument();
    // capacity caption
    expect(within(section()).getByText(/100 tokens\/sec/i)).toBeInTheDocument();
    expect(within(section()).getByText(/burst 200/i)).toBeInTheDocument();
  });

  it("test_bandwidth_zero_level_is_not_unknown", async () => {
    // A real over-throttled bucket (level 0) must show "0 / 200", NOT collapse to "—".
    // Guards against a `!level` falsy-check refactor that would hide a fully-drained key.
    server.use(
      http.get(`${APP}/api/gw/admin/bandwidth`, () =>
        HttpResponse.json({
          enabled: true,
          rate_per_sec: 100,
          burst: 200,
          keys: [{ key_id: "00000000-0000-0000-0000-000000000004", name: "drained", level: 0 }],
        }),
      ),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/drained/i)).toBeInTheDocument();
    });
    expect(within(section()).getByText(/0 \/ 200/)).toBeInTheDocument();
    // it must NOT be rendered as the unknown dash
    expect(within(section()).queryByText("—")).not.toBeInTheDocument();
  });

  it("test_bandwidth_null_level_renders_unknown", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/bandwidth`, () => HttpResponse.json(BW_NULL_LEVEL)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/quiet-key/i)).toBeInTheDocument();
    });
    // null level -> "—" (unknown), never "0" and never "/ 0"
    expect(within(section()).getByText("—")).toBeInTheDocument();
    expect(within(section()).queryByText(/\/ 0/)).not.toBeInTheDocument();
  });

  it("test_bandwidth_disabled_caption", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/bandwidth`, () => HttpResponse.json(BW_DISABLED)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/pacing disabled/i)).toBeInTheDocument();
    });
    // disabled row shows bare "—", not "— / 0"
    expect(within(section()).getByText("—")).toBeInTheDocument();
    expect(within(section()).queryByText(/\/ 0/)).not.toBeInTheDocument();
  });

  it("test_bandwidth_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/bandwidth`, () => HttpResponse.json(BW_EMPTY)),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/no keys/i)).toBeInTheDocument();
    });
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_bandwidth_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/bandwidth`, () =>
        HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 }),
      ),
    );

    renderPanel();

    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_bandwidth_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/bandwidth`,
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
