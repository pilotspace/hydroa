/**
 * keys-dialog-a11y.test.tsx — RED suite for key-budget-governance-ui (v13 task 3)
 *
 * The frozen §3 DIALOG INTERACTION CONTRACT: each /keys dialog (Create key,
 * Revoke-confirm, Rotate-confirm) stays an inline role="dialog" with an accessible
 * name that, while open: moves initial focus inside, traps Tab/Shift-Tab within
 * itself, and closes on Escape (focus restored to the opener). Implemented via a
 * self-contained useFocusTrap hook (no portal, no new dependency).
 *
 * TRUE-RED REASON: CreateKeyDialog / the revoke-confirm currently have NO ESC
 * handler and do NOT move/trap focus, so these assertions fail until Build wires
 * useFocusTrap into the dialogs. Behavior markers (role/name/controls) are
 * unchanged — only keyboard a11y is added.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { CreateKeyDialog } from "@/components/keys/CreateKeyDialog";
import { KeysPage } from "@/components/keys/KeysPage";

function Wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("CreateKeyDialog — keyboard accessibility (focus trap + ESC)", () => {
  const user = userEvent.setup();

  it("test_create_dialog_initial_focus_inside", async () => {
    render(
      <CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />,
    );
    const dialog = screen.getByRole("dialog", { name: /create api key/i });
    // initial focus must land inside the dialog (not on document.body)
    await waitFor(() => {
      expect(dialog.contains(document.activeElement)).toBe(true);
    });
  });

  it("test_create_dialog_escape_closes", async () => {
    const onClose = vi.fn();
    render(
      <CreateKeyDialog isOpen onClose={onClose} onSubmit={vi.fn(async () => {})} />,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("test_create_dialog_focus_trap_wraps", async () => {
    // Focusables OUTSIDE the dialog make the trap assertion discriminating: without a
    // trap, Tab from the last in-dialog control would escape to #outside-after.
    render(
      <div>
        <button data-testid="outside-before">before</button>
        <CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />
        <button data-testid="outside-after">after</button>
      </div>,
    );
    const dialog = screen.getByRole("dialog", { name: /create api key/i });
    const inDialog = (within(dialog).getAllByRole("textbox") as HTMLElement[]).concat(
      within(dialog).getAllByRole("button"),
    );
    expect(inDialog.length).toBeGreaterThan(1);

    // Tab from the LAST in-dialog control must wrap back INTO the dialog, never to #outside-after.
    inDialog[inDialog.length - 1].focus();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByTestId("outside-after"));

    // Shift+Tab from the FIRST in-dialog control must wrap to the last, never to #outside-before.
    inDialog[0].focus();
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByTestId("outside-before"));
  });

  it("test_create_dialog_typing_and_submit_still_work", async () => {
    // The focus-trap must NOT swallow typing or clicks (regression guard for the §1 ⚠)
    const onSubmit = vi.fn(async () => {});
    render(<CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText(/key name/i), "ci-key");
    await user.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("ci-key"));
  });
});

describe("KeysPage revoke-confirm dialog — ESC closes", () => {
  const user = userEvent.setup();

  const KEY_FIXTURE = [
    {
      key_id: "key_abcdef0123456789",
      name: "prod-key",
      prefix: "sk-prod",
      created_at: "2026-06-01T00:00:00Z",
      revoked_at: null,
    },
  ];

  it("test_revoke_confirm_escape_closes", async () => {
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json(KEY_FIXTURE),
      ),
    );
    render(<KeysPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("button", { name: /revoke/i }));
    // confirm dialog open
    const dialog = await screen.findByRole("dialog", { name: /confirm revocation/i });
    expect(dialog).toBeInTheDocument();

    await user.keyboard("{Escape}");
    // ESC closes the confirm dialog (no revocation performed)
    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: /confirm revocation/i }),
      ).not.toBeInTheDocument();
    });
    // the key row is still present (revoke was cancelled, not performed)
    expect(screen.getByText(/prod-key/)).toBeInTheDocument();
  });
});
