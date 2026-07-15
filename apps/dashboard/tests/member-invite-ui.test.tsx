/**
 * tests/member-invite-ui.test.tsx — RED suite for member-invite-ui.
 *
 * Extends the existing /app/members surface (components/members/MembersPage) with
 * a COPY-LINK invite flow (Tin-approved 2026-07-15, no email infra):
 *
 *   - "Invite member" action opens InviteMemberDialog, role select filtered by
 *     assignableRoles(callerRole) (components/members/roles.ts — reused, not
 *     duplicated, from the existing role-reassignment escalation policy).
 *   - POST /admin/invites {email, role} -> 201 {..., token} shows a copyable
 *     accept link `${origin}/invite/${token}` + "expires in 7 days" note. The
 *     token is NEVER retrievable again, so this is the only place it appears.
 *   - 409 (email/invite already exists) surfaces an inline field error.
 *   - A "Pending invites" section (GET /admin/invites) renders each pending
 *     invite's email/role + a live "expires in Nd" countdown chip, and a
 *     Revoke action (DELETE /admin/invites/{id}) that invalidates the list.
 *
 * RED before Build: @/components/members/InviteMemberDialog and
 * @/components/members/PendingInvites do not exist yet -> MODULE_NOT_FOUND,
 * the established true-red convention (see tests/members.test.tsx).
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── RED: these imports fail until Build writes the new components ────────────
import { MembersPage } from "@/components/members/MembersPage";

const APP = "http://localhost:3000";

const OWNER_USERS = {
  users: [{ id: "00000000-0000-0000-0000-000000000001", email: "alice@acme.io", role: "owner" }],
};

const NO_PENDING_INVITES = { invites: [] };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderMembers(callerRole: string, callerUserId?: string) {
  return render(<MembersPage callerRole={callerRole} callerUserId={callerUserId} />, {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>
    ),
  });
}

function nowPlusDays(days: number): string {
  return new Date(Date.now() + days * 86_400_000).toISOString();
}

beforeEach(() => {
  server.use(
    http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(OWNER_USERS)),
    http.get(`${APP}/api/gw/admin/invites`, () => HttpResponse.json(NO_PENDING_INVITES)),
  );
  // jsdom has no Clipboard API by default; `navigator.clipboard` is a
  // getter-only accessor on this jsdom version, so Object.assign throws —
  // redefine the property instead.
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});

describe("MembersPage — invite flow", () => {
  const user = userEvent.setup();

  it("test_invite_button_visible_for_owner_hidden_for_no_assignable_role", async () => {
    renderMembers("owner");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /invite member/i })).toBeInTheDocument();
    });
  });

  it("test_invite_dialog_role_select_filtered_by_caller_role_admin", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(OWNER_USERS)),
    );
    renderMembers("admin");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /invite member/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /invite member/i }));

    const dialog = screen.getByRole("dialog", { name: /invite member/i });
    const roleSelect = within(dialog).getByLabelText(/role/i) as HTMLSelectElement;
    const options = Array.from(roleSelect.options).map((o) => o.value);

    expect(options).not.toContain("owner");
    expect(options).not.toContain("admin");
    expect(options).toContain("operator");
    expect(options).toContain("billing_admin");
    expect(options).toContain("viewer");
    expect(options).toContain("member");
  });

  it("test_invite_dialog_owner_sees_all_six_roles", async () => {
    renderMembers("owner");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /invite member/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /invite member/i }));

    const dialog = screen.getByRole("dialog", { name: /invite member/i });
    const roleSelect = within(dialog).getByLabelText(/role/i) as HTMLSelectElement;
    const options = Array.from(roleSelect.options).map((o) => o.value);

    expect(options).toEqual(
      expect.arrayContaining(["owner", "admin", "operator", "billing_admin", "viewer", "member"]),
    );
  });

  it("test_invite_create_success_shows_copy_link", async () => {
    server.use(
      http.post(`${APP}/api/gw/admin/invites`, async ({ request }) => {
        const body = (await request.json()) as { email: string; role: string };
        expect(body.email).toBe("newbie@acme.io");
        return HttpResponse.json(
          {
            id: "inv-1",
            email: body.email,
            role: body.role,
            status: "pending",
            expires_at: nowPlusDays(7),
            created_at: new Date().toISOString(),
            invited_by_user_id: "00000000-0000-0000-0000-000000000001",
            token: "PLAINTEXT_TOKEN_ONE_TIME",
          },
          { status: 201 },
        );
      }),
    );

    renderMembers("owner");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /invite member/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /invite member/i }));

    const dialog = screen.getByRole("dialog", { name: /invite member/i });
    await user.type(within(dialog).getByLabelText(/email/i), "newbie@acme.io");
    await user.click(within(dialog).getByRole("button", { name: /send invite/i }));

    await waitFor(() => {
      expect(within(dialog).getByText(/invite\/PLAINTEXT_TOKEN_ONE_TIME/i)).toBeInTheDocument();
    });
    expect(within(dialog).getByText(/expires in 7 days/i)).toBeInTheDocument();

    // Copy button writes the full accept link to the clipboard.
    await user.click(within(dialog).getByRole("button", { name: /copy link/i }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("/invite/PLAINTEXT_TOKEN_ONE_TIME"),
      );
    });
  });

  it("test_invite_create_409_inline_error", async () => {
    server.use(
      http.post(`${APP}/api/gw/admin/invites`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Already invited", status: 409, code: "ERR_INVITE_EXISTS" },
          { status: 409 },
        ),
      ),
    );

    renderMembers("owner");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /invite member/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /invite member/i }));

    const dialog = screen.getByRole("dialog", { name: /invite member/i });
    await user.type(within(dialog).getByLabelText(/email/i), "dup@acme.io");
    await user.click(within(dialog).getByRole("button", { name: /send invite/i }));

    await waitFor(() => {
      expect(within(dialog).getByText(/already exists/i)).toBeInTheDocument();
    });
    // Dialog stays open (no copy-link state reached) on error.
    expect(within(dialog).queryByRole("button", { name: /copy link/i })).not.toBeInTheDocument();
  });

  it("test_pending_invites_empty_state", async () => {
    renderMembers("owner");
    await waitFor(() => {
      expect(screen.getByText(/no pending invites/i)).toBeInTheDocument();
    });
  });

  it("test_pending_invites_renders_countdown_chip", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/invites`, () =>
        HttpResponse.json({
          invites: [
            {
              id: "inv-2",
              email: "pending@acme.io",
              role: "member",
              status: "pending",
              expires_at: nowPlusDays(5),
              created_at: new Date().toISOString(),
              invited_by_user_id: "00000000-0000-0000-0000-000000000001",
            },
          ],
        }),
      ),
    );

    renderMembers("owner");

    await waitFor(() => {
      expect(screen.getByText(/pending@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/expires in 5d/i)).toBeInTheDocument();
  });

  it("test_pending_invites_revoke_calls_delete_and_refetches", async () => {
    let deleteCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/invites`, () => {
        if (deleteCalled) return HttpResponse.json(NO_PENDING_INVITES);
        return HttpResponse.json({
          invites: [
            {
              id: "inv-3",
              email: "revokeme@acme.io",
              role: "member",
              status: "pending",
              expires_at: nowPlusDays(3),
              created_at: new Date().toISOString(),
              invited_by_user_id: "00000000-0000-0000-0000-000000000001",
            },
          ],
        });
      }),
      http.delete(`${APP}/api/gw/admin/invites/inv-3`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderMembers("owner");

    await waitFor(() => {
      expect(screen.getByText(/revokeme@acme\.io/i)).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /revoke invite to revokeme@acme\.io/i }));

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });
    // The row is gone (checked via the row's own Revoke button, not a text search —
    // the success banner legitimately echoes the email too, in "Revoked the invite
    // to revokeme@acme.io.").
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /revoke invite to revokeme@acme\.io/i }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText(/no pending invites/i)).toBeInTheDocument();
  });

  it("test_pending_invites_section_hidden_for_non_manager", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json({ users: [] })),
    );
    renderMembers("member");

    await waitFor(() => {
      expect(screen.getByText(/no members yet/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /invite member/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/pending invites/i)).not.toBeInTheDocument();
  });
});
