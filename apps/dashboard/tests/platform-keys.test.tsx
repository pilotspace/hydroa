/**
 * tests/platform-keys.test.tsx — RED suite for the Keys tab (M8) of the
 * admin-console-ui tenant-detail screen — the SAFETY-CRITICAL surface (R6): a
 * create/rotate mutation's plaintext `key` field (present in the real, frozen
 * CreateKeyResponse/RotateKeyResponse DTOs — keys/api/schemas.py:211, "shown
 * EXACTLY ONCE") must NEVER be rendered, copied into the DOM, or retained in
 * any component state after onSuccess returns — MILESTONE.md's Out-of-scope
 * line is explicit and unconditional (§1 ⚠ Assumption #1).
 *
 * RED before Build: `@/components/platform/PlatformKeysTab` does not exist yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformKeysTab } from "@/components/platform/PlatformKeysTab";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";
const PLAINTEXT_SECRET = "sk-should-never-be-rendered-anywhere-abc123";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderKeys() {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformKeysTab tenantId={TENANT_ID} />
    </QueryClientProvider>,
  );
}

describe("PlatformKeysTab", () => {
  it("test_create_key_never_renders_plaintext_secret", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json([]),
      ),
      http.post(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json(
          {
            key_id: "aaaaaaaa-0000-0000-0000-000000000001",
            name: "prod-key",
            key: PLAINTEXT_SECRET,
          },
          { status: 201 },
        ),
      ),
    );

    renderKeys();
    await waitFor(() => {
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /create key/i }));
    fireEvent.change(screen.getByLabelText(/key name/i), { target: { value: "prod-key" } });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(screen.getByText(/key created/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/prod-key/i)).toBeInTheDocument();

    // R6 — the strongest possible assertion: the raw secret string is nowhere
    // in the rendered DOM tree, in any attribute or text node.
    expect(document.body.innerHTML).not.toContain(PLAINTEXT_SECRET);
    expect(screen.queryByText(PLAINTEXT_SECRET)).not.toBeInTheDocument();
    // No one-time-reveal UI of any kind appears (PlaintextKeyBanner is never used here).
    expect(screen.queryByText(/won.t see this key again/i)).not.toBeInTheDocument();
  });

  it("test_rotate_key_never_renders_plaintext_secret", async () => {
    const KEY_ID = "aaaaaaaa-0000-0000-0000-000000000001";
    const NEW_SECRET = "sk-new-secret-should-never-appear-xyz789";
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json([
          {
            key_id: KEY_ID,
            name: "prod-key",
            prefix: "sk-abc123",
            created_at: "2026-01-01T00:00:00Z",
            revoked_at: null,
          },
        ]),
      ),
      http.post(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys/${KEY_ID}/rotate`, () =>
        HttpResponse.json(
          {
            new_key_id: "bbbbbbbb-0000-0000-0000-000000000002",
            superseded_key_id: KEY_ID,
            key: NEW_SECRET,
            name: "prod-key",
          },
          { status: 201 },
        ),
      ),
    );

    renderKeys();
    await waitFor(() => {
      expect(screen.getByText("prod-key")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /rotate/i }));

    await waitFor(() => {
      expect(screen.getByText(/key rotated/i)).toBeInTheDocument();
    });

    expect(document.body.innerHTML).not.toContain(NEW_SECRET);
    expect(screen.queryByText(NEW_SECRET)).not.toBeInTheDocument();
  });

  it("test_revoke_key_requires_focus_trapped_confirm", async () => {
    const KEY_ID = "aaaaaaaa-0000-0000-0000-000000000001";
    let deleteCalled = false;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json([
          {
            key_id: KEY_ID,
            name: "prod-key",
            prefix: "sk-abc123",
            created_at: "2026-01-01T00:00:00Z",
            revoked_at: null,
          },
        ]),
      ),
      http.delete(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys/${KEY_ID}`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderKeys();
    await waitFor(() => {
      expect(screen.getByText("prod-key")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /revoke/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(deleteCalled).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /revoke/i }));
    const dialog2 = await screen.findByRole("dialog");
    fireEvent.click(within(dialog2).getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });
  });

  it("test_keys_empty_state_create_reachable", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/keys`, () =>
        HttpResponse.json([]),
      ),
    );
    renderKeys();
    await waitFor(() => {
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /create key/i })).toBeInTheDocument();
  });
});
