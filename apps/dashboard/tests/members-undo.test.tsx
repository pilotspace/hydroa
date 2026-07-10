/**
 * tests/members-undo.test.tsx — W5 audit fix: reversible role-change safety net.
 *
 * The members role selector mutates on `change` (frozen contract, members.test.tsx's
 * test_members_page_role_change_calls_put: change → immediate PUT). A BLOCKING confirm
 * dialog would break that frozen test AND mismatch this codebase's own pattern — blocking
 * confirms guard IRREVERSIBLE actions (key revoke/rotate, team/member/memory/artifact
 * delete); a role change is REVERSIBLE. So the right safety net is a POST-change UNDO
 * banner: after a successful change it announces what happened and offers one-click revert
 * (PUT the previous role back). change → PUT stays intact, so the frozen contract holds.
 *
 * These are NEW assertions in a SEPARATE file — members.test.tsx (frozen) is left pristine.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { MembersPage } from "@/components/members/MembersPage";

const APP = "http://localhost:3000";

const USERS = {
  users: [
    { id: "00000000-0000-0000-0000-000000000001", email: "alice@acme.io", role: "owner" },
    { id: "00000000-0000-0000-0000-000000000002", email: "bob@acme.io", role: "member" },
  ],
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrap({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function section() {
  return screen.getByRole("region", { name: /member/i });
}

/** Bob's row selector — the one currently valued "member" (alice is "owner"). */
function bobSelect(): HTMLSelectElement {
  const el = Array.from(document.querySelectorAll("select")).find(
    (s) => (s as HTMLSelectElement).value === "member",
  );
  if (!el) throw new Error("bob's role select (value=member) not found");
  return el as HTMLSelectElement;
}

describe("MembersPage — reversible role-change undo (W5)", () => {
  it("test_role_change_still_fires_put_immediately", async () => {
    // Guard the frozen contract from the new file's side: change → PUT, no confirm click.
    let captured: { role: string } | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS)),
      http.put(`${APP}/api/gw/admin/users/:userId/role`, async ({ params, request }) => {
        captured = (await request.json()) as { role: string };
        return HttpResponse.json({ id: params.userId, email: "bob@acme.io", role: captured.role });
      }),
    );
    render(<MembersPage callerRole="owner" />, { wrapper: Wrap });
    await waitFor(() => expect(within(section()).getByText(/bob@acme\.io/i)).toBeInTheDocument());

    fireEvent.change(bobSelect(), { target: { value: "operator" } });

    await waitFor(() => expect(captured?.role).toBe("operator"));
  });

  it("test_undo_banner_appears_after_successful_change", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS)),
      http.put(`${APP}/api/gw/admin/users/:userId/role`, async ({ params, request }) => {
        const body = (await request.json()) as { role: string };
        return HttpResponse.json({ id: params.userId, email: "bob@acme.io", role: body.role });
      }),
    );
    render(<MembersPage callerRole="owner" />, { wrapper: Wrap });
    await waitFor(() => expect(within(section()).getByText(/bob@acme\.io/i)).toBeInTheDocument());

    fireEvent.change(bobSelect(), { target: { value: "operator" } });

    // A status banner announces the change and offers Undo.
    await waitFor(() =>
      expect(within(section()).getByText(/changed .*bob@acme\.io.*operator/i)).toBeInTheDocument(),
    );
    expect(within(section()).getByRole("button", { name: /undo/i })).toBeInTheDocument();
  });

  it("test_undo_reverts_to_previous_role_via_put", async () => {
    const puts: Array<{ userId: string; role: string }> = [];
    server.use(
      http.get(`${APP}/api/gw/admin/users`, () => HttpResponse.json(USERS)),
      http.put(`${APP}/api/gw/admin/users/:userId/role`, async ({ params, request }) => {
        const body = (await request.json()) as { role: string };
        puts.push({ userId: params.userId as string, role: body.role });
        return HttpResponse.json({ id: params.userId, email: "bob@acme.io", role: body.role });
      }),
    );
    render(<MembersPage callerRole="owner" />, { wrapper: Wrap });
    await waitFor(() => expect(within(section()).getByText(/bob@acme\.io/i)).toBeInTheDocument());

    fireEvent.change(bobSelect(), { target: { value: "operator" } });

    const undoBtn = await within(section()).findByRole("button", { name: /undo/i });
    fireEvent.click(undoBtn);

    await waitFor(() => {
      expect(puts.length).toBe(2);
      // second PUT restores the PREVIOUS role (member), for the same user
      expect(puts[1].role).toBe("member");
      expect(puts[1].userId).toBe("00000000-0000-0000-0000-000000000002");
    });
    // banner clears after undo
    await waitFor(() =>
      expect(within(section()).queryByRole("button", { name: /undo/i })).not.toBeInTheDocument(),
    );
  });
});
