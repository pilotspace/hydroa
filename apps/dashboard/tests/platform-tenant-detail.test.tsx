/**
 * tests/platform-tenant-detail.test.tsx — RED suite for Screen 2 of the
 * admin-console-ui task: the superadmin-only tenant-detail shell
 * (/app/platform/tenants/[tenantId] — TASK.md §3 CONTRACT, SCREEN 2).
 *
 * Covers the SHELL-level scenarios (banner, header, tabs, 404, tab isolation,
 * M12 deep-linking); the 4 tab panels' own scenarios live in their own test
 * files (platform-config-budget / platform-keys / platform-members).
 *
 * RED before Build: `@/components/platform/PlatformTenantDetail` does not exist
 * yet -> MODULE_NOT_FOUND.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { useSearchParams } from "next/navigation";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import React from "react";

import { PlatformTenantDetail } from "@/components/platform/PlatformTenantDetail";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";
const PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000099";

const TENANT_ACME = {
  id: TENANT_ID,
  name: "Acme Robotics",
  kind: "standard",
  created_at: "2026-01-15T00:00:00Z",
};

const TENANT_PLATFORM = {
  id: PLATFORM_TENANT_ID,
  name: "Platform Tenant",
  kind: "platform",
  created_at: "2026-01-01T00:00:00Z",
};

const CACHE_OK = { enabled: true, semantic_enabled: false };
const GUARDRAILS_OK = { prompt_injection: null, pii_mask: null };
const BUDGET_OK = { budget_usd_monthly: "500.00", spent_usd_month: "120.00" };
const KEYS_OK: unknown[] = [];
const USERS_OK = { users: [{ id: "u-1", email: "bob@acme.io", role: "admin" }] };

function mockAllTabsHappyPath(tenantId: string, tenant: typeof TENANT_ACME) {
  server.use(
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}`, () => HttpResponse.json(tenant)),
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}/cache`, () =>
      HttpResponse.json(CACHE_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}/guardrails`, () =>
      HttpResponse.json(GUARDRAILS_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}/budget`, () =>
      HttpResponse.json(BUDGET_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}/keys`, () =>
      HttpResponse.json(KEYS_OK),
    ),
    http.get(`${APP}/api/gw/admin/platform/tenants/${tenantId}/users`, () =>
      HttpResponse.json(USERS_OK),
    ),
  );
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderDetail(tenantId: string = TENANT_ID) {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformTenantDetail tenantId={tenantId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Deterministic default for every test unless it overrides — afterEach only
  // clears call history (test-support/mock-cjs-navigation.ts), not a
  // previously-set mockReturnValue, so a stale ?tab= could otherwise leak
  // across tests in this file.
  vi.mocked(useSearchParams).mockReturnValue(
    new URLSearchParams() as ReturnType<typeof useSearchParams>,
  );
});

describe("PlatformTenantDetail — shell", () => {
  it("test_detail_shell_dom_order_banner_then_header_then_tabs", async () => {
    mockAllTabsHappyPath(TENANT_ID, TENANT_ACME);
    renderDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Acme Robotics" })).toBeInTheDocument();
    });

    const banner = screen.getByTestId("platform-safety-banner");
    const heading = screen.getByRole("heading", { level: 1 });
    const tablist = screen.getByRole("tablist");

    // banner precedes h1 precedes tablist, in document order (M4, M11)
    expect(
      banner.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      heading.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("test_safety_banner_persists_across_tab_switches", async () => {
    mockAllTabsHappyPath(TENANT_ID, TENANT_ACME);
    renderDetail();

    await waitFor(() => {
      expect(screen.getByTestId("platform-safety-banner")).toHaveTextContent("Acme Robotics");
    });

    fireEvent.click(screen.getByRole("tab", { name: /budget/i }));
    await waitFor(() => {
      expect(screen.getByText(/monthly budget/i)).toBeInTheDocument();
    });
    expect(screen.getByTestId("platform-safety-banner")).toHaveTextContent("Acme Robotics");

    fireEvent.click(screen.getByRole("tab", { name: /members/i }));
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.getByTestId("platform-safety-banner")).toHaveTextContent("Acme Robotics");
  });

  it("test_safety_banner_platform_tenant_wording", async () => {
    mockAllTabsHappyPath(PLATFORM_TENANT_ID, TENANT_PLATFORM);
    renderDetail(PLATFORM_TENANT_ID);

    await waitFor(() => {
      expect(screen.getByTestId("platform-safety-banner")).toBeInTheDocument();
    });
    const banner = screen.getByTestId("platform-safety-banner");
    // distinct wording for the reserved/self-referential case — not the
    // ordinary-tenant copy ("Viewing: {name} — Platform Admin Mode")
    expect(banner.textContent).toMatch(/own reserved tenant/i);
  });

  it("test_detail_404_full_page_error_no_partial_render", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}`, () =>
        HttpResponse.json(
          { title: "Tenant not found", status: 404, code: "ERR_TENANT_NOT_FOUND" },
          { status: 404 },
        ),
      ),
    );
    renderDetail();

    await waitFor(() => {
      expect(screen.getByText(/tenant not found/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("platform-safety-banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("test_tab_failure_isolated_other_tab_still_loads", async () => {
    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("tab=budget") as ReturnType<typeof useSearchParams>,
    );
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}`, () =>
        HttpResponse.json(TENANT_ACME),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/budget`, () =>
        HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json(KEYS_OK),
      ),
    );
    renderDetail();

    await waitFor(() => {
      expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
    });
    expect(screen.getByTestId("platform-safety-banner")).toHaveTextContent("Acme Robotics");

    fireEvent.click(screen.getByRole("tab", { name: /keys/i }));
    await waitFor(() => {
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument();
    });
    // Isolation proof: Keys loaded independently, no crash/blocked render, and
    // the shell-level banner (never re-fetched/re-mounted per tab) is unaffected.
    // (Budget's own error is no longer asserted present — this hand-rolled Tabs
    // unmounts the inactive TabsContent by design; see TASK.md §5 Known-problem d.)
    expect(screen.getByTestId("platform-safety-banner")).toHaveTextContent("Acme Robotics");
  });

  it("test_tab_deep_link_activates_from_query_param", async () => {
    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("tab=budget") as ReturnType<typeof useSearchParams>,
    );
    mockAllTabsHappyPath(TENANT_ID, TENANT_ACME);
    renderDetail();

    await waitFor(() => {
      expect(screen.getByText(/monthly budget/i)).toBeInTheDocument();
    });
    // Config tab's content is absent — Budget is active on FIRST render, not
    // merely reachable after a click.
    expect(screen.queryByLabelText(/enable response cache/i)).not.toBeInTheDocument();
  });

  it("test_tab_invalid_or_absent_query_param_falls_back_to_config", async () => {
    mockAllTabsHappyPath(TENANT_ID, TENANT_ACME);

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams() as ReturnType<typeof useSearchParams>,
    );
    const { unmount } = renderDetail();
    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });
    unmount();

    vi.mocked(useSearchParams).mockReturnValue(
      new URLSearchParams("tab=bogus") as ReturnType<typeof useSearchParams>,
    );
    renderDetail();
    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });
  });

  it("test_tab_switch_calls_router_replace_with_new_tab_no_push", async () => {
    mockAllTabsHappyPath(TENANT_ID, TENANT_ACME);
    renderDetail();

    await waitFor(() => {
      expect(screen.getByLabelText(/enable response cache/i)).toBeInTheDocument();
    });

    const router = getRouterMock();
    fireEvent.click(screen.getByRole("tab", { name: /members/i }));

    await waitFor(() => {
      expect(router.replace).toHaveBeenCalled();
    });
    const lastCall = router.replace.mock.calls.at(-1)!;
    expect(String(lastCall[0])).toContain("tab=members");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("test_long_tenant_name_never_truncates_in_detail", async () => {
    const LONG_NAME = "Northwind Traders International Holdings Group Ltd.";
    const longTenant = { ...TENANT_ACME, name: LONG_NAME };
    mockAllTabsHappyPath(TENANT_ID, longTenant);
    renderDetail();

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(LONG_NAME);
    });
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.className).not.toContain("truncate");
    const banner = screen.getByTestId("platform-safety-banner");
    expect(banner).toHaveTextContent(LONG_NAME);
    expect(banner.className ?? "").not.toContain("truncate");
  });

  it("test_non_superadmin_403_standard_error", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}`, () =>
        HttpResponse.json(
          { title: "Insufficient role for this action", status: 403, code: "ERR_AUTH_FORBIDDEN" },
          { status: 403 },
        ),
      ),
    );
    renderDetail();

    await waitFor(() => {
      expect(screen.getByText(/insufficient role/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });
});
