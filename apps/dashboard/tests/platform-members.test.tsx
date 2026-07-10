/**
 * tests/platform-members.test.tsx — RED suite for the Members tab (M9) of the
 * admin-console-ui tenant-detail screen. Mirrors MembersPage.tsx's list +
 * per-row role-reassign + self-guard EXACTLY (dictated by the frozen backend
 * route set — platform_users_router.py exposes only GET ""/PUT "/{id}/role",
 * confirmed by reading it directly this session), scoped to the TARGET tenant.
 *
 * RED before Build: `@/components/platform/PlatformMembersTab` does not exist yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformMembersTab } from "@/components/platform/PlatformMembersTab";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";
const CALLER_ID = "00000000-0000-0000-0000-000000000099";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderMembers() {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformMembersTab tenantId={TENANT_ID} />
    </QueryClientProvider>,
  );
}

describe("PlatformMembersTab", () => {
  it("test_reassign_role_calls_put_scoped_to_target_tenant", async () => {
    let capturedUserId: string | null = null;
    let capturedBody: unknown = null;
    server.use(
      http.get(`${APP}/api/auth/me`, () =>
        HttpResponse.json({
          user_id: CALLER_ID,
          tenant_id: "platform-tenant-id",
          email: "root@platform.internal",
          role: "superadmin",
          exp: null,
        }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json({
          users: [
            { id: "u-1", email: "bob@acme.io", role: "member" },
            { id: CALLER_ID, email: "root@platform.internal", role: "superadmin" },
          ],
        }),
      ),
      http.put(
        `${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users/:userId/role`,
        async ({ request, params }) => {
          capturedUserId = params.userId as string;
          capturedBody = await request.json();
          return HttpResponse.json({ id: "u-1", email: "bob@acme.io", role: "operator" });
        },
      ),
    );

    renderMembers();
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    const select = screen.getByLabelText(/assign role to bob@acme\.io/i);
    fireEvent.change(select, { target: { value: "operator" } });

    await waitFor(() => {
      expect(capturedBody).toEqual({ role: "operator" });
    });
    expect(capturedUserId).toBe("u-1");
  });

  it("test_self_row_role_selector_disabled_your_account_label", async () => {
    server.use(
      http.get(`${APP}/api/auth/me`, () =>
        HttpResponse.json({
          user_id: CALLER_ID,
          tenant_id: "platform-tenant-id",
          email: "root@platform.internal",
          role: "superadmin",
          exp: null,
        }),
      ),
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json({
          users: [{ id: CALLER_ID, email: "root@platform.internal", role: "superadmin" }],
        }),
      ),
    );

    renderMembers();
    await waitFor(() => {
      expect(screen.getByText(/root@platform\.internal/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/\(your account\)/i)).toBeInTheDocument();
    expect(
      screen.queryByLabelText(/assign role to root@platform\.internal/i),
    ).not.toBeInTheDocument();
  });

  it("test_members_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
        HttpResponse.json({ users: [] }),
      ),
    );
    renderMembers();
    await waitFor(() => {
      expect(screen.getByText(/no members/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
