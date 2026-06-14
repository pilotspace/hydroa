/**
 * tests-bff/spend-breakdown.test.tsx — RED suite for v15 governance-completion-ui (spend depth).
 *
 * DEEPENS SpendPage with a group_by selector (None|Key|Team) + a key_id filter
 * (dropdown from GET /admin/keys) + a polymorphic breakdown Table. The gateway
 * GET /admin/spend already accepts group_by ∈ {key_id, team_id} and key_id.
 *
 * The breakdown is polymorphic: key rows {key_id, …, cost_usd} vs team rows
 * {team_id|null, team_name|null, …, cost_usd, ledger_cost_usd} vs null (no table).
 * The existing totals/buckets/chart MUST keep rendering.
 *
 * RED before build: the group_by/key_id controls + breakdown table don't exist yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { axe } from "vitest-axe";
import { server } from "./mocks/server";
import React from "react";

import { SpendPage } from "@/components/spend/SpendPage";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

const BASE_TOTALS = {
  bucket_start: "2026-06-01T00:00:00Z",
  bucket_end: "2026-06-11T00:00:00Z",
  requests: 8,
  prompt_tokens: 800,
  completion_tokens: 400,
  cost_usd: "5.00",
};
const BASE_BUCKETS = [
  {
    bucket_start: "2026-06-01T00:00:00Z",
    requests: 8,
    prompt_tokens: 800,
    completion_tokens: 400,
    cost_usd: "5.00",
  },
];

const SPEND_NONE = {
  window: "month",
  bucket_size: "month",
  totals: BASE_TOTALS,
  buckets: BASE_BUCKETS,
  breakdown: null,
};

const SPEND_BY_KEY = {
  ...SPEND_NONE,
  breakdown: [
    {
      key_id: "key-001",
      requests: 5,
      prompt_tokens: 500,
      completion_tokens: 250,
      cost_usd: "3.50",
    },
    {
      key_id: "key-002",
      requests: 3,
      prompt_tokens: 300,
      completion_tokens: 150,
      cost_usd: "1.50",
    },
  ],
};

const SPEND_BY_TEAM = {
  ...SPEND_NONE,
  breakdown: [
    {
      team_id: "team-alpha",
      team_name: "Alpha",
      requests: 6,
      prompt_tokens: 600,
      completion_tokens: 300,
      cost_usd: "4.00",
      ledger_cost_usd: "4.10",
    },
    {
      team_id: null,
      team_name: null,
      requests: 2,
      prompt_tokens: 200,
      completion_tokens: 100,
      cost_usd: "1.00",
      ledger_cost_usd: "1.00",
    },
  ],
};

const KEYS = [
  { key_id: "key-001", name: "prod", prefix: "sk-1a2b" },
  { key_id: "key-002", name: "staging", prefix: "sk-3c4d" },
];

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const keysGet = () => http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json(KEYS));

/** Route /admin/spend by the group_by query param. */
type SpendBody = Record<string, unknown>;
function spendRouter(byKey: SpendBody, byTeam: SpendBody, none: SpendBody = SPEND_NONE) {
  return http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
    const gb = new URL(request.url).searchParams.get("group_by");
    if (gb === "key_id") return HttpResponse.json(byKey);
    if (gb === "team_id") return HttpResponse.json(byTeam);
    return HttpResponse.json(none);
  });
}

describe("SpendPage — breakdown depth", () => {
  it("test_group_by_key_breakdown_table", async () => {
    const user = userEvent.setup();
    server.use(keysGet(), spendRouter(SPEND_BY_KEY, SPEND_BY_TEAM));
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "key_id");

    const table = await screen.findByRole("table", { name: /spend by key/i });
    expect(within(table).getByText("key-001")).toBeInTheDocument();
    expect(within(table).getByText("key-002")).toBeInTheDocument();
    expect(within(table).getByText("3.50")).toBeInTheDocument();
    // totals/buckets still render
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("5.00");
    expect(screen.getAllByTestId("spend-bucket").length).toBe(1);
  });

  it("test_group_by_team_breakdown_table", async () => {
    const user = userEvent.setup();
    server.use(keysGet(), spendRouter(SPEND_BY_KEY, SPEND_BY_TEAM));
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "team_id");

    const table = await screen.findByRole("table", { name: /spend by team/i });
    expect(within(table).getByText("Alpha")).toBeInTheDocument();
    // a null team renders as "(no team)"
    expect(within(table).getByText(/\(no team\)/i)).toBeInTheDocument();
    // ledger cost column present
    expect(within(table).getByText("4.10")).toBeInTheDocument();
  });

  it("test_key_filter_adds_key_id", async () => {
    const user = userEvent.setup();
    let lastUrl = "";
    server.use(
      keysGet(),
      http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json(SPEND_NONE);
      }),
    );
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    await user.selectOptions(screen.getByRole("combobox", { name: /filter by key/i }), "key-001");
    await waitFor(() =>
      expect(new URL(lastUrl).searchParams.get("key_id")).toBe("key-001"),
    );
  });

  it("test_group_by_none_no_table", async () => {
    server.use(keysGet(), spendRouter(SPEND_BY_KEY, SPEND_BY_TEAM));
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");
    // default group_by None → no breakdown table
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("test_spend_invalid_query_422", async () => {
    const user = userEvent.setup();
    server.use(
      keysGet(),
      http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
        const gb = new URL(request.url).searchParams.get("group_by");
        if (gb === "key_id") return problem("Invalid group_by", 422, "ERR_PAYLOAD_INVALID");
        return HttpResponse.json(SPEND_NONE);
      }),
    );
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "key_id");
    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid group_by/i);
    // prior view intact (contract §1 / reject "bad_query"): totals stay visible
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("5.00");
    expect(screen.getAllByTestId("spend-bucket").length).toBe(1);
  });

  it("test_spend_cross_tenant_key_404", async () => {
    const user = userEvent.setup();
    server.use(
      keysGet(),
      http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
        const kid = new URL(request.url).searchParams.get("key_id");
        if (kid) return problem("Key not found", 404, "ERR_KEY_NOT_FOUND");
        return HttpResponse.json(SPEND_NONE);
      }),
    );
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    await user.selectOptions(screen.getByRole("combobox", { name: /filter by key/i }), "key-001");
    expect(await screen.findByRole("alert")).toHaveTextContent(/key not found/i);
    // prior view intact (contract §1 / reject "cross_tenant_key"): totals stay visible
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("5.00");
  });

  it("test_group_by_none_hides_table_after_select", async () => {
    const user = userEvent.setup();
    server.use(keysGet(), spendRouter(SPEND_BY_KEY, SPEND_BY_TEAM));
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    // select Key → table shows
    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "key_id");
    await screen.findByRole("table", { name: /spend by key/i });

    // back to None → the breakdown table is gone (no stale table)
    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "");
    await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
    // totals/buckets still render
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("5.00");
  });

  it("test_window_change_error_keeps_prior_view_with_matching_label", async () => {
    // An error triggered by a WINDOW change must keep the prior view intact AND
    // label the totals with the window of the DATA SHOWN (prior), never the
    // pending selector value — i.e. no "Totals (day)" over month numbers.
    const user = userEvent.setup();
    server.use(
      keysGet(),
      http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
        const w = new URL(request.url).searchParams.get("window");
        if (w === "day") return problem("Spend unavailable", 500, "ERR_INTERNAL");
        return HttpResponse.json(SPEND_NONE); // window=month → ok (window:"month")
      }),
    );
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");
    expect(screen.getByText(/totals \(month\)/i)).toBeInTheDocument();

    // switch the time window to day → 500
    await user.selectOptions(screen.getByTestId("window-selector"), "day");
    expect(await screen.findByRole("alert")).toHaveTextContent(/spend unavailable/i);
    // prior view intact: totals still visible AND labelled with the shown data's
    // window ("month"), NOT the pending selector value ("day")
    expect(screen.getByTestId("totals-cost")).toHaveTextContent("5.00");
    expect(screen.getByText(/totals \(month\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/totals \(day\)/i)).not.toBeInTheDocument();
  });

  it("test_error_then_recovery_clears_alert", async () => {
    // recovering from a bad query (select a valid option) clears the alert and
    // shows the fresh breakdown — viewData tracks the new success, not the ref.
    const user = userEvent.setup();
    server.use(
      keysGet(),
      http.get(`${APP}/api/gw/admin/spend`, ({ request }) => {
        const gb = new URL(request.url).searchParams.get("group_by");
        if (gb === "team_id") return problem("Invalid group_by", 422, "ERR_PAYLOAD_INVALID");
        if (gb === "key_id") return HttpResponse.json(SPEND_BY_KEY);
        return HttpResponse.json(SPEND_NONE);
      }),
    );
    render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");

    // bad query → alert, prior view intact, no table
    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "team_id");
    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid group_by/i);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    // recover by choosing a valid group_by → alert clears, fresh key table shows
    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "key_id");
    await screen.findByRole("table", { name: /spend by key/i });
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("test_spend_breakdown_axe_clean", async () => {
    const user = userEvent.setup();
    server.use(keysGet(), spendRouter(SPEND_BY_KEY, SPEND_BY_TEAM));
    const { container } = render(<SpendPage />, { wrapper: Wrapper });
    await screen.findByTestId("totals-cost");
    await user.selectOptions(screen.getByRole("combobox", { name: /group by/i }), "key_id");
    await screen.findByRole("table", { name: /spend by key/i });
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});
