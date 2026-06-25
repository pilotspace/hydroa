/**
 * tests/members.test.tsx — RED suite for the /app/members dashboard page.
 *
 * MembersPage fetches GET /admin/users (via the BFF catch-all /api/gw/admin/users)
 * and renders the tenant user list with a role selector per user.
 *
 * Key contract requirements:
 *  - Renders a list of users with id, email, role
 *  - Each user has a role selector offering ONLY the caller's assignable tiers
 *    (admin sees no owner/admin options; owner sees all 6)
 *  - Calls PUT /admin/users/{id}/role on selection
 *  - States: loading / error / empty / success
 *  - One h1 (a11y)
 *  - No serious/critical axe violations
 *
 * RED before Build: `@/components/members/MembersPage` does not exist yet →
 * MODULE_NOT_FOUND, the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { MembersPage } from "@/components/members/MembersPage";

const APP = "http://localhost:3000";

const USERS_RESPONSE_OWNER = {
  users: [
    { id: "00000000-0000-0000-0000-000000000001", email: "alice@acme.io", role: "owner" },
    { id: "00000000-0000-0000-0000-000000000002", email: "bob@acme.io", role: "member" },
    { id: "00000000-0000-0000-0000-000000000003", email: "carol@acme.io", role: "operator" },
  ],
};

const USERS_RESPONSE_ADMIN = {
  users: [
    { id: "00000000-0000-0000-0000-000000000001", email: "alice@acme.io", role: "owner" },
    { id: "00000000-0000-0000-0000-000000000002", email: "bob@acme.io", role: "member" },
  ],
};

const USERS_EMPTY = { users: [] };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function WrapperOwner({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

function renderMembersOwner() {
  return render(<MembersPage callerRole="owner" />, { wrapper: WrapperOwner });
}

function renderMembersAdmin() {
  return render(<MembersPage callerRole="admin" />, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={makeQueryClient()}>
        {children}
      </QueryClientProvider>
    ),
  });
}

/** Scope assertions to the members section. */
function section() {
  return screen.getByRole("region", { name: /member/i });
}

describe("MembersPage", () => {
  it("test_members_page_renders_user_list", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_RESPONSE_OWNER)),
    );

    renderMembersOwner();

    await waitFor(() => {
      expect(within(section()).getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    const sec = section();
    expect(within(sec).getByText(/bob@acme\.io/i)).toBeInTheDocument();
    expect(within(sec).getByText(/carol@acme\.io/i)).toBeInTheDocument();
  });

  it("test_members_page_has_one_h1", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_RESPONSE_OWNER)),
    );

    renderMembersOwner();

    await waitFor(() => {
      const headings = document.querySelectorAll("h1");
      expect(headings.length).toBe(1);
    });
  });

  it("test_members_page_owner_sees_all_six_roles_in_selector", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_RESPONSE_OWNER)),
    );

    renderMembersOwner();

    await waitFor(() => {
      expect(within(section()).getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    // Owner should see owner/admin/operator/billing_admin/viewer/member options
    // For the non-self user (bob), the selector should offer all 6 tiers
    // We look for select elements with the expected options
    const selects = document.querySelectorAll("select");
    // At least one select should exist (for bob or carol)
    expect(selects.length).toBeGreaterThan(0);

    // Find the select for bob (non-owner role target)
    const options = Array.from(document.querySelectorAll("option")).map((o) => o.value);
    expect(options).toContain("owner");
    expect(options).toContain("admin");
    expect(options).toContain("operator");
    expect(options).toContain("billing_admin");
    expect(options).toContain("viewer");
    expect(options).toContain("member");
  });

  it("test_members_page_admin_does_not_see_owner_admin_in_selector", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_RESPONSE_ADMIN)),
      http.get(`${APP}/api/auth/me`, () =>
        HttpResponse.json({
          user_id: "00000000-0000-0000-0000-000000000099",
          tenant_id: "00000000-0000-0000-0000-000000000099",
          email: "adminuser@acme.io",
          role: "admin",
          exp: null,
        })
      ),
    );

    renderMembersAdmin();

    await waitFor(() => {
      expect(within(section()).getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    // Admin should NOT see owner/admin options in any selector
    const options = Array.from(document.querySelectorAll("option")).map((o) => o.value);
    // Should have operator/billing_admin/viewer/member but NOT owner or admin
    expect(options).not.toContain("owner");
    expect(options).not.toContain("admin");
    expect(options).toContain("operator");
    expect(options).toContain("billing_admin");
    expect(options).toContain("viewer");
    expect(options).toContain("member");
  });

  it("test_members_page_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_EMPTY)),
    );

    renderMembersOwner();

    await waitFor(() => {
      expect(within(section()).getByText(/no members yet/i)).toBeInTheDocument();
    });
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_members_page_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () =>
        HttpResponse.json({ title: "Forbidden", status: 403 }, { status: 403 }),
      ),
    );

    renderMembersOwner();

    await waitFor(() => {
      expect(within(section()).getByText(/forbidden/i)).toBeInTheDocument();
    });
  });

  it("test_members_page_loading_state", async () => {
    server.use(
      http.get(
        `${APP}/api/gw/admin/users`,
        () => new Promise<never>(() => { /* never resolves */ }),
      ),
    );

    renderMembersOwner();

    await waitFor(() => {
      const spinner =
        within(section()).queryByRole("status") ??
        document.querySelector("[aria-busy='true']") ??
        document.querySelector(".animate-pulse, .animate-spin");
      expect(spinner).toBeTruthy();
    });
  });

  it("test_members_page_role_change_calls_put", async () => {
    let capturedBody: unknown = null;
    let capturedUserId: string | null = null;

    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS_RESPONSE_OWNER)),
      http.put(`${APP}/api/gw/admin/users/:userId/role`, async ({ params, request }) => {
        capturedUserId = params.userId as string;
        capturedBody = await request.json();
        return HttpResponse.json({
          id: capturedUserId,
          email: "bob@acme.io",
          role: "operator",
        });
      }),
    );

    renderMembersOwner();

    await waitFor(() => {
      expect(within(section()).getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    // Find the select for bob's row and change to "operator"
    // Bob is "00000000-0000-0000-0000-000000000002"
    const selects = document.querySelectorAll("select");
    expect(selects.length).toBeGreaterThan(0);

    // Fire change on the first select associated with a non-owner user
    fireEvent.change(selects[0], { target: { value: "operator" } });

    await waitFor(() => {
      expect(capturedBody).toBeTruthy();
      expect((capturedBody as Record<string, string>).role).toBe("operator");
    });
  });
});
