/**
 * tests/invite-member-dialog-email.test.tsx — RED suite for InviteMemberDialog's
 * additive email-delivery copy line (transactional-email TASK.md §4, M11/M12).
 *
 * The copy-link success state (link, Copy button, Done button) is UNCHANGED (byte-
 * identical) — only ONE additive line is asserted here, gated on the new, optional
 * `email_delivery_channel` field on the POST /admin/invites 201 response.
 *
 * Dispatch-honest wording (§3 CONTRACT "Decided at freeze" #2): the line must read
 * "We've emailed this link to {email}." (or equivalent honest, non-"delivered" phrasing)
 * — never claim confirmed delivery, since fire-and-forget can't know that by response time.
 *
 * RED reason: InviteMemberDialog already exists (this is an ADDITIVE change to a shipped
 * component, not a brand-new one), so red here is an assertion failure — the extra line
 * is simply not rendered yet — not a MODULE_NOT_FOUND.
 */

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { InviteMemberDialog } from "@/components/members/InviteMemberDialog";

const APP = "http://localhost:3000";
const INVITES_URL = `${APP}/api/gw/admin/invites`;

const BASE_RESPONSE = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "new@co.io",
  role: "member",
  status: "pending",
  expires_at: "2026-08-01T00:00:00Z",
  created_at: "2026-07-17T00:00:00Z",
  invited_by_user_id: "00000000-0000-0000-0000-000000000002",
  token: "plaintext-token-abc123",
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderDialog() {
  const queryClient = makeQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <InviteMemberDialog isOpen={true} onClose={() => {}} callerRole="owner" />
    </QueryClientProvider>
  );
}

async function submitInvite(email: string) {
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
}

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// TEST 1: dialog shows the extra line only for smtp delivery                          [M11]
// ---------------------------------------------------------------------------

describe("InviteMemberDialog — email_delivery_channel copy (M11)", () => {
  it("shows the emailed-link line when email_delivery_channel is 'smtp'", async () => {
    server.use(
      http.post(INVITES_URL, () =>
        HttpResponse.json({ ...BASE_RESPONSE, email_delivery_channel: "smtp" }, { status: 201 })
      )
    );

    renderDialog();
    await submitInvite("new@co.io");

    await waitFor(() => {
      expect(screen.getByText(/we've emailed this link to/i)).toBeInTheDocument();
    });
    // Dispatch-honest wording — never overclaims confirmed delivery.
    expect(screen.queryByText(/delivered/i)).not.toBeInTheDocument();

    // The copy-link block is byte-identical: still exactly the client-built link.
    const code = screen.getByText(`http://localhost:3000/invite/${BASE_RESPONSE.token}`);
    expect(code).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy link/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^done$/i })).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // TEST 2: dialog shows no extra line for console delivery                          [M11]
  // -------------------------------------------------------------------------

  it("shows no extra line when email_delivery_channel is 'console'", async () => {
    server.use(
      http.post(INVITES_URL, () =>
        HttpResponse.json({ ...BASE_RESPONSE, email_delivery_channel: "console" }, { status: 201 })
      )
    );

    renderDialog();
    await submitInvite("new@co.io");

    await waitFor(() => {
      expect(
        screen.getByText(`http://localhost:3000/invite/${BASE_RESPONSE.token}`)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/we've emailed this link to/i)).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // TEST 3: FE type stays backward-compatible — field entirely absent            [M12]
  // -------------------------------------------------------------------------

  it("shows no extra line and no runtime error when email_delivery_channel is absent", async () => {
    server.use(
      http.post(INVITES_URL, () => HttpResponse.json(BASE_RESPONSE, { status: 201 }))
    );

    renderDialog();
    await submitInvite("new@co.io");

    await waitFor(() => {
      expect(
        screen.getByText(`http://localhost:3000/invite/${BASE_RESPONSE.token}`)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/we've emailed this link to/i)).not.toBeInTheDocument();
  });
});
