/**
 * tests-bff/nav-role-filter.test.tsx — RED suite for v17 task nav-role-filter.
 *
 * Role-based Primary-nav visibility: a `member` must not SEE links to pages whose
 * GET 403s on member (/models, /teams, /routing — all require_owner_or_admin in the
 * gateway). usage/spend/keys/settings stay (member-viewable). admin/owner see all 7.
 * Unknown role fails OPEN (all 7) — the gateway still enforces RBAC on navigate.
 *
 * AppShell takes an optional `role` prop (presentational); DashboardShell ("use
 * client") feeds it from useCurrentUser().role. The nav filter is UX-only — no
 * gateway/BFF contract change, no new network call.
 *
 * RED before Build: `@/components/dashboard-shell` does not exist → the file fails
 * at collect (MODULE_NOT_FOUND), the established true-red convention; and AppShell
 * has no `role` prop yet, so a member would still see all 7.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { AppShell } from "@/components/ui";
// ── RED: this import fails until Build creates components/dashboard-shell.tsx ──
import { DashboardShell } from "@/components/dashboard-shell";

const APP = "http://localhost:3000";
const ADMIN_ONLY = [/models/i, /teams/i, /routing/i];
const MEMBER_OK = [/usage/i, /spend/i, /api keys/i, /settings/i];
const ALL_SEVEN = [...MEMBER_OK.slice(0, 3), ...ADMIN_ONLY, /settings/i];

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function nav() {
  return screen.getByRole("navigation", { name: /primary/i });
}

describe("AppShell — role-based nav visibility", () => {
  it("test_member_hides_admin_only_links", () => {
    render(
      <AppShell role="member">
        <div>content</div>
      </AppShell>,
    );
    const n = nav();
    for (const re of ADMIN_ONLY) {
      expect(within(n).queryByRole("link", { name: re })).not.toBeInTheDocument();
    }
    for (const re of MEMBER_OK) {
      expect(within(n).getByRole("link", { name: re })).toBeInTheDocument();
    }
    // exactly the 4 member-OK links — guards against an accidental extra/missing
    // minRole tag silently dropping a member-visible link.
    expect(within(n).getAllByRole("link")).toHaveLength(4);
  });

  it("test_admin_sees_all_links", () => {
    render(
      <AppShell role="admin">
        <div>content</div>
      </AppShell>,
    );
    const n = nav();
    for (const re of ALL_SEVEN) {
      expect(within(n).getByRole("link", { name: re })).toBeInTheDocument();
    }
    expect(within(n).getAllByRole("link")).toHaveLength(7);
  });

  it("test_owner_sees_all_links", () => {
    render(
      <AppShell role="owner">
        <div>content</div>
      </AppShell>,
    );
    expect(within(nav()).getAllByRole("link")).toHaveLength(7);
  });

  it("test_unknown_role_fails_open", () => {
    const { unmount } = render(
      <AppShell role={null}>
        <div>content</div>
      </AppShell>,
    );
    expect(within(nav()).getAllByRole("link")).toHaveLength(7);
    unmount();

    // no role prop at all → also fail-open (preserves the prior AppShell behavior)
    render(
      <AppShell>
        <div>content</div>
      </AppShell>,
    );
    expect(within(nav()).getAllByRole("link")).toHaveLength(7);
  });
});

describe("DashboardShell — wires role from useCurrentUser", () => {
  it("test_dashboard_shell_filters_from_current_user", async () => {
    server.use(
      http.get(`${APP}/api/auth/me`, () =>
        HttpResponse.json({
          user_id: "u-1",
          tenant_id: "t-1",
          email: "bob@acme.io",
          role: "member",
          exp: Math.floor(Date.now() / 1000) + 86400,
        }),
      ),
    );
    render(
      <DashboardShell>
        <div>content</div>
      </DashboardShell>,
      { wrapper: Wrapper },
    );

    const n = nav();
    // after the current-user query resolves to member, the admin-only links drop out
    await waitFor(() => {
      expect(within(n).queryByRole("link", { name: /models/i })).not.toBeInTheDocument();
    });
    expect(within(n).queryByRole("link", { name: /teams/i })).not.toBeInTheDocument();
    expect(within(n).queryByRole("link", { name: /routing/i })).not.toBeInTheDocument();
    // member-OK links remain — exactly 4 after the role resolves to member
    expect(within(n).getByRole("link", { name: /usage/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /settings/i })).toBeInTheDocument();
    expect(within(n).getAllByRole("link")).toHaveLength(4);
  });
});
