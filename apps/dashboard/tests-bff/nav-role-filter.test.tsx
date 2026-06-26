/**
 * tests-bff/nav-role-filter.test.tsx — RED suite for v17 task nav-role-filter.
 *
 * Role-based Primary-nav visibility: a `member` must not SEE links to pages whose
 * GET 403s on member (/models, /teams, /routing, /alerts, /health — all
 * require_owner_or_admin in the gateway). usage/spend/keys/settings stay (member-viewable).
 * admin/owner see all 9. Unknown role fails OPEN (all 9) — the gateway still enforces RBAC.
 *
 * UPDATED by alerts-events-viewer: the admin-only "Alerts" link superseded the prior 7 → 8.
 * UPDATED by upstream-health-view: the admin-only "Health" link (GET /admin/health/upstreams
 * is owner/admin-only) supersedes 8 → admin/owner/unknown now see 9.
 * UPDATED by audit-log-surface: the admin-only "Audit" link (GET /admin/audit is
 * AUDIT_READ gated: owner/admin/operator) supersedes 9 → admin/owner/unknown now see 10.
 * UPDATED by rbac-admin-ui: the admin-only "Members" link (/app/members — MEMBERS_MANAGE)
 * supersedes 10 → admin/owner/unknown now see 11.
 * UPDATED by slo-dashboard: the admin-only "SLO" link (/app/slo — OPS_READ enforced server-side)
 * supersedes 11 → admin/owner/unknown now see 12.
 * UPDATED by chat-workspace-page (v40): a MEMBER-visible "Chat" link (/app/chat, NO minRole) is
 * added at the TOP of the nav → member now sees 5 (was 4); admin/owner/unknown now see 13 (was 12).
 * UPDATED by voice-playground (v40): a MEMBER-visible "Voice" link (/app/voice, NO minRole) is
 * added after Chat → member now sees 6 (was 5); admin/owner/unknown now see 14 (was 13).
 * UPDATED by memory-workspace (v44): a MEMBER-visible "Memory" link (/app/memory, NO minRole) is
 * added after Voice → member now sees 7 (was 6); admin/owner/unknown now see 15 (was 14).
 * UPDATED by artifacts-workspace (v45): a MEMBER-visible "Artifacts" link (/app/artifacts, NO minRole) is
 * added near Memory → member now sees 8 (was 7); admin/owner/unknown now see 16 (was 15).
 * UPDATED by vision-workspace (v46): a MEMBER-visible "Vision" link (/app/vision, NO minRole) is
 * added after Artifacts → member now sees 9 (was 8); admin/owner/unknown now see 17 (was 16).
 * UPDATED by video-workspace (v47): a MEMBER-visible "Video" link (/app/video, NO minRole) is
 * added after Vision → member now sees 10 (was 9); admin/owner/unknown now see 18 (was 17).
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
const ADMIN_ONLY = [/models/i, /teams/i, /members/i, /routing/i, /alerts/i, /audit/i, /health/i, /^slo$/i];
const MEMBER_OK = [/^chat$/i, /^voice$/i, /^memory$/i, /^artifacts$/i, /^vision$/i, /^video$/i, /usage/i, /spend/i, /api keys/i, /settings/i];
const ALL_EIGHTEEN = [/^chat$/i, /^voice$/i, /^memory$/i, /^artifacts$/i, /^vision$/i, /^video$/i, /usage/i, /spend/i, /api keys/i, ...ADMIN_ONLY, /settings/i];

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
    // exactly the 10 member-OK links (Chat + Voice + Memory + Artifacts + Vision + Video + Usage + Spend + API Keys + Settings) —
    // guards against an accidental extra/missing minRole tag silently dropping a
    // member-visible link.
    expect(within(n).getAllByRole("link")).toHaveLength(10);
  });

  it("test_admin_sees_all_links", () => {
    render(
      <AppShell role="admin">
        <div>content</div>
      </AppShell>,
    );
    const n = nav();
    for (const re of ALL_EIGHTEEN) {
      expect(within(n).getByRole("link", { name: re })).toBeInTheDocument();
    }
    expect(within(n).getAllByRole("link")).toHaveLength(18);
  });

  it("test_owner_sees_all_links", () => {
    render(
      <AppShell role="owner">
        <div>content</div>
      </AppShell>,
    );
    expect(within(nav()).getAllByRole("link")).toHaveLength(18);
  });

  it("test_unknown_role_fails_open", () => {
    const { unmount } = render(
      <AppShell role={null}>
        <div>content</div>
      </AppShell>,
    );
    expect(within(nav()).getAllByRole("link")).toHaveLength(18);
    unmount();

    // no role prop at all → also fail-open (preserves the prior AppShell behavior)
    render(
      <AppShell>
        <div>content</div>
      </AppShell>,
    );
    expect(within(nav()).getAllByRole("link")).toHaveLength(18);
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
    // member-OK links remain — exactly 10 after the role resolves to member
    expect(within(n).getByRole("link", { name: /^chat$/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /^voice$/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /^memory$/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /^artifacts$/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /^vision$/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /^video$/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /usage/i })).toBeInTheDocument();
    expect(within(n).getByRole("link", { name: /settings/i })).toBeInTheDocument();
    expect(within(n).getAllByRole("link")).toHaveLength(10);
  });
});
