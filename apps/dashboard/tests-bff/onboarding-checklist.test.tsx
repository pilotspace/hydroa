/**
 * tests-bff/onboarding-checklist.test.tsx — RED suite for activation-quickstart
 * TASK.md §3 v1 #6, M6 + M7 (OnboardingChecklist, mounted in OverviewPage above the
 * KPI section).
 *
 * RED before Build: @/components/overview/OnboardingChecklist does not exist
 * (MODULE_NOT_FOUND). New file — does not edit tests-bff/overview-home.test.tsx.
 *
 * The shared default handlers (tests-bff/mocks/handlers.ts) were extended with
 * GET /admin/provider-keys -> {keys:[]} and GET /admin/invites -> {invites:[]} so
 * every PRE-EXISTING overview-home.test.tsx test (which now also renders this
 * checklist, role defaults to "owner") stays green without editing that file.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { OverviewPage } from "@/components/overview/OverviewPage";

const APP = "http://localhost:3000";
const ROOT = resolve(__dirname, "..");

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function meHandler(role: string, tenantId = "tenant-a") {
  return http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "u-1",
      tenant_id: tenantId,
      email: "ada@acme.io",
      role,
      exp: Math.floor(Date.now() / 1000) + 86400,
    }),
  );
}

/** Minimal spend/usage/budget so OverviewPage clears its own loading/error gate. */
function mockCoreMetrics(opts: { totalRequests?: number } = {}) {
  const totalRequests = opts.totalRequests ?? 0;
  server.use(
    http.get(`${APP}/api/gw/admin/spend`, () =>
      HttpResponse.json({
        window: "month",
        totals: { requests: totalRequests, prompt_tokens: 0, completion_tokens: 0, cost_usd: "0.00" },
        buckets: [],
      }),
    ),
    http.get(`${APP}/api/gw/admin/usage`, () =>
      HttpResponse.json({
        total_cost_usd: "0.00",
        total_requests: totalRequests,
        total_prompt_tokens: 0,
        total_completion_tokens: 0,
        records: [],
      }),
    ),
    http.get(`${APP}/api/gw/admin/budget`, () =>
      HttpResponse.json({ budget_usd_monthly: null, spent_usd_month: "0.00" }),
    ),
  );
}

function mockKeys(keys: unknown[]) {
  server.use(http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json(keys)));
}
function mockProviderKeys(keys: unknown[]) {
  server.use(http.get(`${APP}/api/gw/admin/provider-keys`, () => HttpResponse.json({ keys })));
}
function mockInvites(invites: unknown[]) {
  server.use(http.get(`${APP}/api/gw/admin/invites`, () => HttpResponse.json({ invites })));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OnboardingChecklist — owner, empty tenant (M6)", () => {
  it("test_checklist_owner_sees_all_four_steps_empty_tenant", async () => {
    server.use(meHandler("owner"));
    mockCoreMetrics({ totalRequests: 0 });
    mockKeys([]);
    mockProviderKeys([]);
    mockInvites([]);

    const { container } = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");

    const checklist = await screen.findByTestId("onboarding-checklist");
    expect(checklist).toBeInTheDocument();
    expect(within(checklist).getByText(/create.*key/i)).toBeInTheDocument();
    expect(within(checklist).getByText(/first call|make a call/i)).toBeInTheDocument();
    expect(within(checklist).getByText(/provider key|byok|bring your own key/i)).toBeInTheDocument();
    expect(within(checklist).getByText(/invite/i)).toBeInTheDocument();

    // sits ABOVE the KPI section
    const kpiSection = container.querySelector('[aria-label="Key metrics"]') as Node;
    expect(kpiSection).toBeTruthy();
    // eslint-disable-next-line no-bitwise
    expect(checklist.compareDocumentPosition(kpiSection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("OnboardingChecklist — member role never triggers a 403 (M6 edge case)", () => {
  it("test_checklist_member_role_no_403_two_steps", async () => {
    server.use(meHandler("member"));
    mockCoreMetrics({ totalRequests: 0 });
    mockKeys([]);

    let providerKeysCalled = false;
    let invitesCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/provider-keys`, () => {
        providerKeysCalled = true;
        return HttpResponse.json({ type: "about:blank", title: "Forbidden", status: 403 }, { status: 403 });
      }),
      http.get(`${APP}/api/gw/admin/invites`, () => {
        invitesCalled = true;
        return HttpResponse.json({ type: "about:blank", title: "Forbidden", status: 403 }, { status: 403 });
      }),
    );
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    const checklist = await screen.findByTestId("onboarding-checklist");

    expect(within(checklist).getByText(/create.*key/i)).toBeInTheDocument();
    expect(within(checklist).getByText(/first call|make a call/i)).toBeInTheDocument();
    expect(within(checklist).queryByText(/provider key|byok|bring your own key/i)).not.toBeInTheDocument();
    expect(within(checklist).queryByText(/^invite/i)).not.toBeInTheDocument();

    // never issued — enabled:false, not issued-then-caught
    await new Promise((r) => setTimeout(r, 20));
    expect(providerKeysCalled).toBe(false);
    expect(invitesCalled).toBe(false);
    expect(consoleError).not.toHaveBeenCalled();
    void container;
  });
});

describe("OnboardingChecklist — reflects real state (M6)", () => {
  it("test_checklist_reflects_real_state_partial", async () => {
    server.use(meHandler("owner"));
    mockCoreMetrics({ totalRequests: 3 });
    mockKeys([{ key_id: "k1", name: "prod", prefix: "sk-p", created_at: "2026-01-01T00:00:00Z", revoked_at: null }]);
    mockProviderKeys([]);
    mockInvites([]);

    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    const checklist = await screen.findByTestId("onboarding-checklist");

    expect(within(checklist).getByTestId("step-create_key")).toHaveAttribute("data-complete", "true");
    expect(within(checklist).getByTestId("step-first_call")).toHaveAttribute("data-complete", "true");
    expect(within(checklist).getByTestId("step-byok")).toHaveAttribute("data-complete", "false");
    expect(within(checklist).getByTestId("step-invite")).toHaveAttribute("data-complete", "false");

    // still visible — not all VISIBLE steps complete
    expect(screen.getByTestId("onboarding-checklist")).toBeInTheDocument();
  });
});

describe("OnboardingChecklist — dismiss persists per-tenant (M7)", () => {
  it("test_checklist_dismiss_persists_per_tenant", async () => {
    const user = userEvent.setup();
    server.use(meHandler("owner", "tenant-a"));
    mockCoreMetrics({ totalRequests: 3 });
    mockKeys([{ key_id: "k1", name: "prod" }]);
    mockProviderKeys([]);
    mockInvites([]);

    const render1 = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    await screen.findByTestId("onboarding-checklist");

    await user.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => expect(screen.queryByTestId("onboarding-checklist")).not.toBeInTheDocument());
    render1.unmount();

    // "reload" tenant A — a fresh mount, same tenant_id, same localStorage (not cleared)
    server.use(meHandler("owner", "tenant-a"));
    mockCoreMetrics({ totalRequests: 3 });
    mockKeys([{ key_id: "k1", name: "prod" }]);
    mockProviderKeys([]);
    mockInvites([]);
    const render2 = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("onboarding-checklist")).not.toBeInTheDocument();
    render2.unmount();

    // a DIFFERENT tenant (tenant-b) still shows its own checklist independently
    server.use(meHandler("owner", "tenant-b"));
    mockCoreMetrics({ totalRequests: 3 });
    mockKeys([{ key_id: "k1", name: "prod" }]);
    mockProviderKeys([]);
    mockInvites([]);
    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    expect(await screen.findByTestId("onboarding-checklist")).toBeInTheDocument();
  });
});

describe("OnboardingChecklist — auto-hides on full completion (M7)", () => {
  it("test_checklist_auto_hides_on_completion", async () => {
    // Control: with 3/4 steps done (invite still missing), the checklist DOES
    // render — proves this is a real completion check, not absence-by-default.
    server.use(meHandler("owner"));
    mockCoreMetrics({ totalRequests: 5 });
    mockKeys([{ key_id: "k1", name: "prod" }]);
    mockProviderKeys([{ provider: "openai", enabled: true }]);
    mockInvites([]);
    const { unmount } = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    expect(await screen.findByTestId("onboarding-checklist")).toBeInTheDocument();
    unmount();

    // Now all 4 steps complete -> auto-hides, with no dismiss click and no write.
    server.use(meHandler("owner"));
    mockCoreMetrics({ totalRequests: 5 });
    mockKeys([{ key_id: "k1", name: "prod" }]);
    mockProviderKeys([{ provider: "openai", enabled: true }]);
    mockInvites([{ id: "inv-1", email: "x@acme.io", status: "pending" }]);

    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.queryByTestId("onboarding-checklist")).not.toBeInTheDocument();
    expect(localStorage.getItem("hydroa_onboarding_dismissed_tenant-a")).toBeNull();
  });
});

describe("OnboardingChecklist — SSR-safety (M7 edge case)", () => {
  it("test_checklist_no_lazy_localstorage_initializer", () => {
    const src = readFileSync(resolve(ROOT, "components/overview/OnboardingChecklist.tsx"), "utf-8");
    // never a lazy useState initializer reading localStorage
    expect(src).not.toMatch(/useState\(\s*\(\)\s*=>[^;]*localStorage/s);
    // localStorage IS read, but only inside a useEffect
    const effectBlocks = src.match(/useEffect\(([\s\S]*?)\}, \[[^\]]*\]\)/g) ?? [];
    const localStorageInEffect = effectBlocks.some((b) => /localStorage/.test(b));
    expect(localStorageInEffect).toBe(true);
  });

  it("no hydration-mismatch console.error during mount for a previously-dismissed tenant", async () => {
    server.use(meHandler("owner", "tenant-a"));
    mockCoreMetrics({ totalRequests: 0 });
    mockKeys([]);
    mockProviderKeys([]);
    mockInvites([]);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    // Control: WITHOUT the dismiss flag, tenant-a's checklist DOES render —
    // proves the hidden state below is real localStorage-driven logic, not
    // the checklist simply never having existed.
    const { unmount } = render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    expect(await screen.findByTestId("onboarding-checklist")).toBeInTheDocument();
    unmount();

    localStorage.setItem("hydroa_onboarding_dismissed_tenant-a", "1");
    render(<OverviewPage />, { wrapper: Wrapper });
    await screen.findByText("Total Requests");
    await waitFor(() => expect(screen.queryByTestId("onboarding-checklist")).not.toBeInTheDocument());

    const hydrationWarnings = consoleError.mock.calls.filter((c) =>
      String(c[0]).toLowerCase().includes("hydrat"),
    );
    expect(hydrationWarnings).toHaveLength(0);
  });
});
