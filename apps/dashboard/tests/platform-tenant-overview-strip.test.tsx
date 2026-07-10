/**
 * tests/platform-tenant-overview-strip.test.tsx — RED suite for
 * `tenant-overview-strip` (TASK.md M1-M8, R1-R5): the always-visible summary
 * region on the tenant-detail screen showing plan+seat-cap, seats-used,
 * active-keys, and budget, each field loading/failing independently by
 * reusing the 4 tabs' own existing React Query cache keys — no new backend
 * endpoint (Tier 1, frontend-only).
 *
 * Two describe blocks:
 *   - "OverviewTile — pure-props states" (M5): the exported pure-props tile
 *     sub-component, rendered directly with hand-crafted isLoading/isError/
 *     value props — zero QueryClient/msw needed (CONVENTIONS.md v13 folded
 *     testability lesson).
 *   - "PlatformTenantOverviewStrip — 4 tiles" (M2-M4, M6-M7, R1-R4): the real
 *     component wired to 4 independent useQuery calls via msw.
 *
 * M1 (DOM-order mount site inside PlatformTenantDetail) and R5/M8 (the 4
 * existing tab suites stay green) are covered in their own existing files —
 * see tests/platform-tenant-detail.test.tsx's new
 * test_strip_mounts_between_pageheader_and_tabs_and_persists_across_tabs, and
 * the unmodified tests/platform-{plan-tab,members,keys,config-budget}.test.tsx.
 *
 * RED before Build: `@/components/platform/PlatformTenantOverviewStrip` does
 * not exist yet -> MODULE_NOT_FOUND.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { bffGet } from "@/lib/bff-client";
import fs from "node:fs";
import path from "node:path";
import React from "react";

import {
  PlatformTenantOverviewStrip,
  OverviewTile,
} from "@/components/platform/PlatformTenantOverviewStrip";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";

const PLAN_OK = {
  tenant_id: TENANT_ID,
  plan: { id: "aaaaaaaa-0000-0000-0000-000000000002", name: "team", display_name: "Team" },
  seat_cap: 25,
};
const USERS_OK = {
  users: [
    { id: "u-1", email: "bob@acme.io", role: "admin" },
    { id: "u-2", email: "amy@acme.io", role: "member" },
  ],
};
const KEYS_OK = [
  {
    key_id: "kid-1",
    name: "prod",
    prefix: "sk-abc",
    created_at: "2026-01-01T00:00:00Z",
    revoked_at: null,
  },
  {
    key_id: "kid-2",
    name: "old",
    prefix: "sk-def",
    created_at: "2026-01-01T00:00:00Z",
    revoked_at: "2026-02-01T00:00:00Z",
  },
];
const BUDGET_OK = { budget_usd_monthly: "500.00", spent_usd_month: "120.00" };

function mockAllFourHappyPath() {
  server.use(
    http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
      HttpResponse.json(PLAN_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
      HttpResponse.json(USERS_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
      HttpResponse.json(KEYS_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
      HttpResponse.json(BUDGET_OK),
    ),
  );
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderStrip(client: QueryClient = makeQueryClient()) {
  return render(
    <QueryClientProvider client={client}>
      <PlatformTenantOverviewStrip tenantId={TENANT_ID} />
    </QueryClientProvider>,
  );
}

// ── OverviewTile — pure-props states (M5) ─────────────────────────────────
// No QueryClientProvider/msw at all: loading/error/success is entirely a
// function of props (CONVENTIONS.md v13 folded lesson), so each state is
// deterministically testable in isolation.
describe("OverviewTile — pure-props states", () => {
  it("test_overview_tile_loading_renders_loading_primitive", () => {
    render(
      <OverviewTile
        label="Plan"
        isLoading={true}
        isError={false}
        error={null}
        onRetry={() => {}}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).toHaveAccessibleName(/loading plan/i);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_overview_tile_error_renders_error_state_with_retry", () => {
    let retried = false;
    render(
      <OverviewTile
        label="Budget"
        isLoading={false}
        isError={true}
        error={new Error("Boom")}
        onRetry={() => {
          retried = true;
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Boom");
    const retryButton = screen.getByRole("button", { name: /retry/i });
    expect(retryButton.tagName).toBe("BUTTON");
    retryButton.click();
    expect(retried).toBe(true);
  });

  it("test_overview_tile_success_renders_statcard_flat_variant", () => {
    render(
      <OverviewTile
        label="Seats used"
        isLoading={false}
        isError={false}
        error={null}
        onRetry={() => {}}
        value={7}
        footer="footer text"
        valueTestId="test-tile-value"
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Seats used")).toBeInTheDocument();
    expect(screen.getByTestId("test-tile-value")).toHaveTextContent("7");
    expect(screen.getByText("footer text")).toBeInTheDocument();
    const card = screen.getByText("Seats used").closest("[data-variant]")!;
    expect(card).toHaveAttribute("data-variant", "flat");
  });
});

// ── PlatformTenantOverviewStrip — 4 tiles wired to real queries ───────────
describe("PlatformTenantOverviewStrip — renders exactly 4 tiles (M2, R4)", () => {
  it("test_strip_renders_exactly_four_tiles_nothing_else", async () => {
    mockAllFourHappyPath();
    renderStrip();

    const region = await screen.findByRole("region", { name: /tenant overview/i });
    await waitFor(() => {
      expect(within(region).getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    });

    expect(within(region).getByText("Plan")).toBeInTheDocument();
    expect(within(region).getByText("Seats used")).toBeInTheDocument();
    expect(within(region).getByText("Active keys")).toBeInTheDocument();
    expect(within(region).getByText("Budget")).toBeInTheDocument();

    // R4 — no Tier-3 kind/plan filter or other new IA sneaks into the Strip.
    expect(within(region).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(region).queryByRole("searchbox")).not.toBeInTheDocument();
    expect(within(region).queryAllByRole("button", { name: /filter/i })).toHaveLength(0);
  });
});

describe("PlatformTenantOverviewStrip — per-tile query identity (M3)", () => {
  it("test_plan_tile_uses_byte_identical_query_key_and_url", async () => {
    mockAllFourHappyPath();
    const client = makeQueryClient();
    renderStrip(client);

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    });
    const entry = client.getQueryCache().find({ queryKey: ["platform-tenant-plan", TENANT_ID] });
    expect(entry).toBeTruthy();
    expect(entry!.state.data).toEqual(PLAN_OK);
    expect(screen.getByText("Seat cap: 25")).toBeInTheDocument();
  });

  it("test_users_tile_uses_byte_identical_query_key_and_displays_length", async () => {
    mockAllFourHappyPath();
    const client = makeQueryClient();
    renderStrip(client);

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
    });
    const entry = client.getQueryCache().find({ queryKey: ["platform-tenant-users", TENANT_ID] });
    expect(entry).toBeTruthy();
    expect(entry!.state.data).toEqual(USERS_OK);
  });

  it("test_keys_tile_uses_byte_identical_query_key_and_counts_non_revoked", async () => {
    mockAllFourHappyPath();
    const client = makeQueryClient();
    renderStrip(client);

    // KEYS_OK has 2 rows, one revoked -> active count is 1.
    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-keys-value")).toHaveTextContent("1");
    });
    const entry = client.getQueryCache().find({ queryKey: ["platform-tenant-keys", TENANT_ID] });
    expect(entry).toBeTruthy();
    expect(entry!.state.data).toEqual(KEYS_OK);
  });

  it("test_budget_tile_uses_byte_identical_query_key_and_url", async () => {
    mockAllFourHappyPath();
    const client = makeQueryClient();
    renderStrip(client);

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
    });
    const entry = client.getQueryCache().find({ queryKey: ["platform-tenant-budget", TENANT_ID] });
    expect(entry).toBeTruthy();
    expect(entry!.state.data).toEqual(BUDGET_OK);
    // Own reasonable footer wording (not frozen) — deliberately avoids the
    // literal phrase "monthly budget" so it never collides with
    // platform-tenant-detail.test.tsx's own bare page-wide
    // getByText(/monthly budget/i) assertions against PlatformBudgetTab's own
    // "Monthly Budget" StatCard label once both are mounted together.
    expect(screen.getByText("Cap: 500.00")).toBeInTheDocument();
  });
});

describe("PlatformTenantOverviewStrip — cache-key reuse, the risk this task owns (R2)", () => {
  it("test_plan_tile_query_key_shared_with_plan_tab_dedupes_fetch", async () => {
    let planFetchCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () => {
        planFetchCount += 1;
        return HttpResponse.json(PLAN_OK);
      }),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json(USERS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json(KEYS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json(BUDGET_OK),
      ),
    );
    const client = makeQueryClient();

    // Simulates PlatformPlanTab being mounted at the same time, sharing the
    // Strip's cache entry via the IDENTICAL key array — this is the exact
    // cache-key-reuse contract M3/R2 exist to guarantee: a diverged key
    // (e.g. "overview-plan") would double the fetch count below.
    function SimulatedPlanTabConsumer() {
      useQuery({
        queryKey: ["platform-tenant-plan", TENANT_ID],
        queryFn: () => bffGet(`/admin/platform/tenants/${TENANT_ID}/plan`),
        retry: false,
      });
      return null;
    }

    render(
      <QueryClientProvider client={client}>
        <PlatformTenantOverviewStrip tenantId={TENANT_ID} />
        <SimulatedPlanTabConsumer />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    });
    expect(planFetchCount).toBe(1);
  });
});

describe("PlatformTenantOverviewStrip — tile isolation + scoped retry (M2)", () => {
  it("test_one_tile_failure_leaves_other_three_intact_and_retry_is_scoped", async () => {
    let keysFetchCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json(PLAN_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json(USERS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json(BUDGET_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () => {
        keysFetchCount += 1;
        return HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 });
      }),
    );
    renderStrip();

    // The other 3 tiles load their data...
    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    });
    expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
    expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
    // ...while Active keys shows its own error, never blanking the other 3.
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/internal server error/i);
    expect(keysFetchCount).toBe(1);

    // Retry is scoped to THAT tile's own query only: clicking it must refetch
    // ONLY the keys endpoint, never the other 3 (no unexpected refetch fires —
    // msw's onUnhandledRequest:"error" would also catch an errant new URL).
    const retryButton = within(alert.parentElement!).getByRole("button", { name: /retry/i });
    retryButton.click();
    await waitFor(() => {
      expect(keysFetchCount).toBe(2);
    });
    // Still just the one alert, still the same unaffected data for the other 3.
    expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
    expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
  });
});

describe("PlatformTenantOverviewStrip — every tile's retry is independently scoped (M5)", () => {
  // Deliberately ONE tile failing at a time (mirrors the Keys-tile test in the
  // describe block above) rather than all 4 simultaneously: lib/resilient-
  // fetch.ts's circuit breaker is keyed by ORIGIN, not per-endpoint
  // (failureThreshold defaults to 5) — 4 simultaneous failures + several
  // retries against the SAME origin trips that shared breaker mid-test
  // (observed directly: a later tile's own mocked title got replaced by the
  // breaker's synthesized "Service temporarily unavailable"), which is
  // resilient-fetch correctly doing its job, not a bug in this component —
  // but it makes a stacked multi-endpoint-failure test an unrealistic,
  // self-inflicted flake. One real user tile failing at a time, each in its
  // own test (fresh breaker state via tests/setup.ts's own
  // afterEach(() => __resetBreakers())), is both more realistic and reliable.
  it("test_plan_tile_failure_retry_is_scoped_to_plan_only", async () => {
    let planFetchCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () => {
        planFetchCount += 1;
        return HttpResponse.json({ title: "Plan failed", status: 500 }, { status: 500 });
      }),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json(USERS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json(KEYS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json(BUDGET_OK),
      ),
    );
    renderStrip();

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
    });
    expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Plan failed");
    expect(planFetchCount).toBe(1);

    within(alert.parentElement!).getByRole("button", { name: /retry/i }).click();
    await waitFor(() => {
      expect(planFetchCount).toBe(2);
    });
    expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
    expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
  });

  it("test_users_tile_failure_retry_is_scoped_to_users_only", async () => {
    let usersFetchCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json(PLAN_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () => {
        usersFetchCount += 1;
        return HttpResponse.json({ title: "Users failed", status: 500 }, { status: 500 });
      }),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json(KEYS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json(BUDGET_OK),
      ),
    );
    renderStrip();

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    });
    expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Users failed");
    expect(usersFetchCount).toBe(1);

    within(alert.parentElement!).getByRole("button", { name: /retry/i }).click();
    await waitFor(() => {
      expect(usersFetchCount).toBe(2);
    });
    expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
  });

  it("test_budget_tile_failure_retry_is_scoped_to_budget_only", async () => {
    let budgetFetchCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json(PLAN_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json(USERS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json(KEYS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () => {
        budgetFetchCount += 1;
        return HttpResponse.json({ title: "Budget failed", status: 500 }, { status: 500 });
      }),
    );
    renderStrip();

    await waitFor(() => {
      expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    });
    expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Budget failed");
    expect(budgetFetchCount).toBe(1);

    within(alert.parentElement!).getByRole("button", { name: /retry/i }).click();
    await waitFor(() => {
      expect(budgetFetchCount).toBe(2);
    });
    expect(screen.getByTestId("platform-overview-plan-value")).toHaveTextContent("Team");
    expect(screen.getByTestId("platform-overview-users-value")).toHaveTextContent("2");
  });
});

describe("PlatformTenantOverviewStrip — flat token wrapper (M6)", () => {
  it("test_strip_wrapper_renders_flat_variant_no_aurora_styling", async () => {
    mockAllFourHappyPath();
    renderStrip();

    const region = await screen.findByRole("region", { name: /tenant overview/i });
    expect(region).toHaveAttribute("data-variant", "flat");
    expect(region.className).toContain("rounded-[var(--radius-flat-card)]");
    expect(region.className).not.toContain("border-border");
    expect(region.className).not.toContain("shadow-lg");
    expect(region.className).not.toContain("rounded-2xl");
  });
});

describe("PlatformTenantOverviewStrip — accessible landmark (M7)", () => {
  it("test_strip_is_a_named_region_landmark_with_native_retry_button", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/plan`, () =>
        HttpResponse.json(PLAN_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json(USERS_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json(BUDGET_OK),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 }),
      ),
    );
    renderStrip();

    // A named landmark, distinct from the Tabs region (role="tablist" is a
    // different landmark entirely — this component never renders one).
    const region = await screen.findByRole("region", { name: /tenant overview/i });
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(within(region).getByRole("alert")).toBeInTheDocument();
    });
    // Native <button> — keyboard-operable by default, no bespoke click handler surface.
    const retryButton = within(region).getByRole("button", { name: /retry/i });
    expect(retryButton.tagName).toBe("BUTTON");
  });
});

describe("PlatformTenantOverviewStrip — type reuse, no local duplicate (M4, R3)", () => {
  it("test_strip_imports_the_four_types_and_declares_no_local_duplicate", () => {
    const source = fs.readFileSync(
      path.resolve(__dirname, "../components/platform/PlatformTenantOverviewStrip.tsx"),
      "utf-8",
    );
    expect(source).toMatch(/TenantPlanResponse/);
    expect(source).toMatch(/from\s+["']\.\/PlatformPlanTab["']/);
    expect(source).toMatch(/UsersListResponse/);
    expect(source).toMatch(/from\s+["']\.\/PlatformMembersTab["']/);
    expect(source).toMatch(/PlatformApiKey/);
    expect(source).toMatch(/from\s+["']\.\/PlatformKeysTab["']/);
    expect(source).toMatch(/BudgetData/);
    expect(source).toMatch(/from\s+["']\.\/PlatformBudgetTab["']/);

    // R3 — no second, locally-duplicated interface declaration for any of the
    // 4 shapes (the import lines above use "TenantPlanResponse" etc. as a
    // symbol reference, never followed by its own "interface <Name>" body).
    expect(source).not.toMatch(/\binterface\s+TenantPlanResponse\b/);
    expect(source).not.toMatch(/\binterface\s+UsersListResponse\b/);
    expect(source).not.toMatch(/\binterface\s+PlatformApiKey\b/);
    expect(source).not.toMatch(/\binterface\s+BudgetData\b/);
  });
});

describe("PlatformTenantOverviewStrip — no combined backend endpoint (R1)", () => {
  it("test_only_the_four_known_urls_are_ever_requested", async () => {
    mockAllFourHappyPath();
    const seenPaths = new Set<string>();
    const listener = ({ request }: { request: Request }) => {
      seenPaths.add(new URL(request.url).pathname);
    };
    server.events.on("request:start", listener);

    try {
      renderStrip();
      await waitFor(() => {
        expect(screen.getByTestId("platform-overview-budget-value")).toHaveTextContent("120.00");
      });
      await waitFor(() => {
        expect(seenPaths.size).toBe(4);
      });
      expect(seenPaths).toEqual(
        new Set([
          `/api/gw/admin/platform/tenants/${TENANT_ID}/plan`,
          `/api/gw/admin/platform/tenants/${TENANT_ID}/users`,
          `/api/gw/admin/platform/tenants/${TENANT_ID}/keys`,
          `/api/gw/admin/platform/tenants/${TENANT_ID}/budget`,
        ]),
      );
    } finally {
      server.events.removeListener("request:start", listener);
    }
  });
});
