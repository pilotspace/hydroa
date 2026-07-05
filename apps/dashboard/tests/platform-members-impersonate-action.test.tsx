/**
 * tests/platform-members-impersonate-action.test.tsx — RED suite for the new
 * per-row "Impersonate" action on the ALREADY-EXISTING PlatformMembersTab
 * (impersonation-ui TASK.md §3 CONTRACT Part C, M9): a NEW optional `tenantKind`
 * prop (fails CLOSED — the action renders for NO row — on anything other than
 * exactly "customer") and a new per-row action, not rendered for the caller's own
 * row or any role==="superadmin" row (R3), disabled with an inline explanation
 * while useImpersonationStatus() reports active:true (R4).
 *
 * A NEW file — NOT tests/platform-members.test.tsx, which never passes the new
 * `tenantKind` prop and needs ZERO changes: the new action's fail-closed default
 * (absent/unrecognized tenantKind -> hidden for every row) is exactly what keeps
 * that existing file passing unmodified.
 *
 * RED failure mode: `@/components/platform/PlatformMembersTab` IMPORTS
 * successfully today (the file already exists) — this reds for a clean
 * LOGIC-ASSERTION reason (the tenantKind prop / new action do not exist yet on
 * today's component), never a MODULE_NOT_FOUND. Every "hidden" assertion below is
 * deliberately paired with either a same-test count check (getAllByRole THROWS on
 * zero matches, so a test can never be accidentally green just because the
 * feature doesn't exist yet) or an explicit same-test control render that DOES
 * expect the button to show — proving the "hidden" result is a real signal, not
 * a vacuous "nothing is built yet" false-pass.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
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

function mockCaller() {
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
  );
}

function mockUsers(users: Array<{ id: string; email: string; role: string }>) {
  server.use(
    http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/users`, () =>
      HttpResponse.json({ users }),
    ),
  );
}

function mockImpersonationStatus(active: boolean) {
  server.use(http.get(`${APP}/api/platform/impersonation`, () => HttpResponse.json({ active })));
}

function renderMembers(tenantKind?: string) {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformMembersTab tenantId={TENANT_ID} tenantKind={tenantKind} />
    </QueryClientProvider>,
  );
}

describe("PlatformMembersTab — Impersonate action", () => {
  it("test_impersonate_action_visible_for_eligible_row", async () => {
    mockCaller();
    mockUsers([{ id: "u-1", email: "bob@acme.io", role: "member" }]);
    mockImpersonationStatus(false);

    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    const button = screen.getByRole("button", { name: /impersonate/i });
    expect(button).toBeInTheDocument();
    expect(button).toBeEnabled();
  });

  it("test_impersonate_action_hidden_for_superadmin_role_row", async () => {
    mockCaller();
    // One eligible row (bob) + one non-self superadmin row. Exactly ONE button
    // must exist (bob's) — never two — isolating the role-based exclusion from
    // the self-row exclusion (covered separately below). getAllByRole THROWS on
    // zero matches, so this cannot pass vacuously before Build.
    mockUsers([
      { id: "u-1", email: "bob@acme.io", role: "member" },
      { id: "u-other-superadmin", email: "other-root@platform.internal", role: "superadmin" },
    ]);
    mockImpersonationStatus(false);

    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/other-root@platform\.internal/i)).toBeInTheDocument();
    });

    const buttons = screen.getAllByRole("button", { name: /impersonate/i });
    expect(buttons).toHaveLength(1);
  });

  it("test_impersonate_action_hidden_when_tenant_kind_not_customer", async () => {
    mockCaller();
    mockUsers([{ id: "u-1", email: "bob@acme.io", role: "member" }]);
    mockImpersonationStatus(false);

    const first = renderMembers("platform");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /impersonate/i })).not.toBeInTheDocument();
    first.unmount();

    const second = renderMembers(undefined);
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /impersonate/i })).not.toBeInTheDocument();
    second.unmount();

    // Control: the SAME row, with the correct tenantKind, DOES show the action —
    // proves the two "hidden" assertions above are a real signal (the harness
    // genuinely CAN render the button), not a vacuous "feature doesn't exist"
    // false-pass. This is the assertion that fails today (genuinely red).
    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /impersonate/i })).toBeInTheDocument();
  });

  it("test_impersonate_action_hidden_for_callers_own_row", async () => {
    mockCaller();
    // The caller's own row (non-superadmin role, so the role-based exclusion
    // above cannot also explain its absence) + one other eligible row. Exactly
    // ONE button must exist (the other row's) — never the caller's own.
    mockUsers([
      { id: CALLER_ID, email: "root@platform.internal", role: "admin" },
      { id: "u-1", email: "bob@acme.io", role: "member" },
    ]);
    mockImpersonationStatus(false);

    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    const buttons = screen.getAllByRole("button", { name: /impersonate/i });
    expect(buttons).toHaveLength(1);
  });

  it("test_impersonate_action_disabled_when_session_already_active", async () => {
    mockCaller();
    mockUsers([{ id: "u-1", email: "bob@acme.io", role: "member" }]);
    mockImpersonationStatus(true);

    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    const button = await screen.findByRole("button", { name: /impersonate/i });
    expect(button).toBeDisabled();
    // R4: proactive prevention carries a short inline explanation, never a
    // silently-disabled control.
    expect(screen.getByText(/already active|already impersonating/i)).toBeInTheDocument();
  });

  it("test_impersonate_confirm_cancel_does_not_mutate", async () => {
    mockCaller();
    mockUsers([{ id: "u-1", email: "bob@acme.io", role: "member" }]);
    mockImpersonationStatus(false);
    let mintCalled = false;
    server.use(
      http.post(`${APP}/api/platform/impersonation`, () => {
        mintCalled = true;
        return HttpResponse.json(
          {
            session_id: "s-1",
            expires_in: 900,
            target: { user_id: "u-1", tenant_id: TENANT_ID, email: "bob@acme.io", role: "member" },
          },
          { status: 201 },
        );
      }),
    );

    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /impersonate/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    expect(mintCalled).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("test_impersonate_confirm_calls_mint_with_correct_tenant_and_user", async () => {
    mockCaller();
    mockUsers([{ id: "u-1", email: "bob@acme.io", role: "member" }]);
    mockImpersonationStatus(false);
    let capturedBody: unknown = null;
    server.use(
      http.post(`${APP}/api/platform/impersonation`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(
          {
            session_id: "s-1",
            expires_in: 900,
            target: { user_id: "u-1", tenant_id: TENANT_ID, email: "bob@acme.io", role: "member" },
          },
          { status: 201 },
        );
      }),
    );

    renderMembers("customer");
    await waitFor(() => {
      expect(screen.getByText(/bob@acme\.io/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /impersonate/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(capturedBody).toEqual({ tenant_id: TENANT_ID, user_id: "u-1" });
    });
  });
});
