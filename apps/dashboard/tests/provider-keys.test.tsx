/**
 * provider-config-ui (v25 task 5) — failing-first (RED) suite.
 *
 * One test per §2 scenario (M1–M7, R1–R5, WIRING). All tests fail at module
 * resolution until @/components/settings/ProviderKeysSettings exists (correct RED).
 *
 * Mirrors tests/keys.test.tsx: vitest + RTL + user-event + msw; per-test handler
 * overrides; QueryClient wrapper (retry:false). All gateway calls go through the BFF
 * proxy at http://localhost:3000/api/gw/* (credentials via cookie — no Authorization
 * header client-side). The backend NEVER returns a secret, so the UI never echoes one.
 *
 * Build contract pinned by these tests (the a11y surface the implementation MUST provide):
 *   - each provider exposes a button named "Configure {provider}" and, when configured,
 *     "Delete {provider}".
 *   - status badges read exactly "Configured" / "Not configured".
 *   - the azure Configure modal has a mode control with radios named /api key/i and /aad|entra/i.
 *   - secret inputs are type=password and start empty (never prefilled).
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

// ── These imports cause MODULE_NOT_FOUND until the build lands — correct RED ──
import { ProviderKeysSettings } from "@/components/settings/ProviderKeysSettings";
import { SettingsPage } from "@/components/settings/SettingsPage";

// ─────────────────────────────────────────────────────────────────────────────

const GW = "http://localhost:3000/api/gw/admin/provider-keys";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderPanel() {
  return render(<ProviderKeysSettings />, { wrapper: Wrapper });
}

interface Status {
  provider: string;
  configured: boolean;
  enabled: boolean;
  auth_mode: string | null;
  updated_at: string;
}

function status(provider: string, auth_mode: string | null = null): Status {
  return {
    provider,
    configured: true,
    enabled: true,
    auth_mode,
    updated_at: "2026-06-17T00:00:00Z",
  };
}

const user = userEvent.setup();

describe("ProviderKeysSettings", () => {
  beforeEach(() => {
    // Default: nothing configured. Individual tests override.
    server.use(http.get(GW, () => HttpResponse.json({ keys: [] })));
  });

  // ── M1 — lists all six providers with status ───────────────────────────────
  it("test_M1_lists_six_with_status", async () => {
    server.use(http.get(GW, () => HttpResponse.json({ keys: [status("openrouter")] })));

    renderPanel();

    await waitFor(() => expect(screen.getByText(/openrouter/i)).toBeInTheDocument());
    for (const p of [/openrouter/i, /openai/i, /anthropic/i, /google/i, /bedrock/i, /azure/i]) {
      expect(screen.getByText(p)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Configured")).toHaveLength(1);
    expect(screen.getAllByText("Not configured")).toHaveLength(5);
    // The configured row surfaces its updated_at (§1 Must, §3 contract).
    expect(
      within(screen.getByText(/openrouter/i).closest("[data-provider]")!).getByText(/2026-06-17/)
    ).toBeInTheDocument();
    // No secret echoed: no password input present while no modal is open.
    expect(document.querySelector("input[type=password]")).toBeNull();
  });

  // ── M2 — configure a bearer provider ───────────────────────────────────────
  it("test_M2_configure_bearer", async () => {
    let getCount = 0;
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get(GW, () => {
        getCount++;
        return HttpResponse.json({ keys: getCount === 1 ? [] : [status("openai")] });
      }),
      http.put(`${GW}/openai`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(status("openai"));
      })
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure openai/i }));
    const secret = await screen.findByLabelText(/secret/i);
    expect((secret as HTMLInputElement).type).toBe("password");
    await user.type(secret, "sk-or-openai-123");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(putBody).toEqual(expect.objectContaining({ secret: "sk-or-openai-123" })));
    // Modal closed → no password input; openai now Configured.
    await waitFor(() => expect(document.querySelector("input[type=password]")).toBeNull());
    await waitFor(() =>
      expect(within(screen.getByText(/openai/i).closest("[data-provider]")!).getByText("Configured")).toBeInTheDocument()
    );
  });

  // ── M3 — configure bedrock with all fields ─────────────────────────────────
  it("test_M3_configure_bedrock", async () => {
    let putBody: Record<string, unknown> | null = null;
    let getCount = 0;
    server.use(
      http.get(GW, () => {
        getCount++;
        return HttpResponse.json({ keys: getCount === 1 ? [] : [status("bedrock")] });
      }),
      http.put(`${GW}/bedrock`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(status("bedrock"));
      })
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure bedrock/i }));
    await user.type(await screen.findByLabelText(/access key id/i), "AKIAEXAMPLE");
    await user.type(screen.getByLabelText(/secret access key/i), "aws-secret-xyz");
    await user.type(screen.getByLabelText(/region/i), "us-east-1");
    await user.type(screen.getByLabelText(/session token/i), "aws-session-xyz");
    // Both cloud secrets are write-only (password) — never plaintext.
    expect((screen.getByLabelText(/secret access key/i) as HTMLInputElement).type).toBe("password");
    expect((screen.getByLabelText(/session token/i) as HTMLInputElement).type).toBe("password");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(putBody).toEqual(
        expect.objectContaining({
          access_key_id: "AKIAEXAMPLE",
          secret_access_key: "aws-secret-xyz",
          region: "us-east-1",
          session_token: "aws-session-xyz",
        })
      )
    );
    // On success the modal closes → no secret survives in the DOM.
    await waitFor(() => expect(document.querySelector("input[type=password]")).toBeNull());
  });

  // ── M4 — configure azure in aad mode ───────────────────────────────────────
  it("test_M4_configure_azure_aad", async () => {
    let putBody: Record<string, unknown> | null = null;
    let getCount = 0;
    server.use(
      http.get(GW, () => {
        getCount++;
        return HttpResponse.json({ keys: getCount === 1 ? [] : [status("azure", "aad")] });
      }),
      http.put(`${GW}/azure`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(status("azure", "aad"));
      })
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure azure/i }));
    await user.click(await screen.findByRole("radio", { name: /aad|entra/i }));
    await user.type(await screen.findByLabelText(/tenant id/i), "tid-123");
    await user.type(screen.getByLabelText(/client id/i), "cid-123");
    await user.type(screen.getByLabelText(/client secret/i), "azure-client-secret");
    await user.type(screen.getByLabelText(/endpoint/i), "https://x.openai.azure.com");
    await user.type(screen.getByLabelText(/api version/i), "2024-02-01");
    // The AAD client secret is write-only (password) — never plaintext.
    expect((screen.getByLabelText(/client secret/i) as HTMLInputElement).type).toBe("password");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(putBody).toEqual(
        expect.objectContaining({
          mode: "aad",
          tenant_id: "tid-123",
          client_id: "cid-123",
          client_secret: "azure-client-secret",
          endpoint: "https://x.openai.azure.com",
          api_version: "2024-02-01",
        })
      )
    );
    await waitFor(() => expect(screen.getByText(/aad/i)).toBeInTheDocument());
    // On success the modal closes → no secret survives in the DOM.
    await waitFor(() => expect(document.querySelector("input[type=password]")).toBeNull());
  });

  // ── M5 — azure mode toggle swaps the visible field set ─────────────────────
  it("test_M5_azure_mode_toggle", async () => {
    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure azure/i }));
    // Default api_key mode: api_key field present, aad-only fields absent.
    await user.click(await screen.findByRole("radio", { name: /api key/i }));
    expect(screen.getByLabelText(/api key/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/tenant id/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/client secret/i)).not.toBeInTheDocument();

    // Switch to aad: aad fields appear, api_key field gone.
    await user.click(screen.getByRole("radio", { name: /aad|entra/i }));
    expect(screen.getByLabelText(/tenant id/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/client secret/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^api key$/i)).not.toBeInTheDocument();
  });

  // ── M6 — delete a configured provider ──────────────────────────────────────
  it("test_M6_delete_confirmed", async () => {
    let deleteCount = 0;
    let getCount = 0;
    server.use(
      http.get(GW, () => {
        getCount++;
        return HttpResponse.json({ keys: getCount === 1 ? [status("openrouter")] : [] });
      }),
      http.delete(`${GW}/openrouter`, () => {
        deleteCount++;
        return new HttpResponse(null, { status: 204 });
      })
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /delete openrouter/i }));
    // Confirm dialog
    await user.click(await screen.findByRole("button", { name: /confirm|yes/i }));

    await waitFor(() => expect(deleteCount).toBe(1));
    await waitFor(() =>
      expect(
        within(screen.getByText(/openrouter/i).closest("[data-provider]")!).getByText("Not configured")
      ).toBeInTheDocument()
    );
  });

  // ── M7 — secret is never prefilled when re-configuring ─────────────────────
  it("test_M7_secret_never_prefilled", async () => {
    server.use(http.get(GW, () => HttpResponse.json({ keys: [status("azure", "aad")] })));

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure azure/i }));
    await user.click(await screen.findByRole("radio", { name: /aad|entra/i }));
    expect((screen.getByLabelText(/client secret/i) as HTMLInputElement).value).toBe("");

    await user.click(screen.getByRole("radio", { name: /api key/i }));
    expect((screen.getByLabelText(/api key/i) as HTMLInputElement).value).toBe("");
  });

  // ── M8 — configure azure in api_key mode (happy path + body shape) ─────────
  it("test_M8_configure_azure_api_key", async () => {
    let putBody: Record<string, unknown> | null = null;
    let getCount = 0;
    server.use(
      http.get(GW, () => {
        getCount++;
        return HttpResponse.json({ keys: getCount === 1 ? [] : [status("azure", "api_key")] });
      }),
      http.put(`${GW}/azure`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(status("azure", "api_key"));
      })
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure azure/i }));
    await user.click(await screen.findByRole("radio", { name: /api key/i }));
    const apiKey = await screen.findByLabelText(/api key/i);
    expect((apiKey as HTMLInputElement).type).toBe("password");
    await user.type(apiKey, "azure-api-key-xyz");
    await user.type(screen.getByLabelText(/endpoint/i), "https://x.openai.azure.com");
    await user.type(screen.getByLabelText(/api version/i), "2024-02-01");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(putBody).toEqual(
        expect.objectContaining({
          mode: "api_key",
          api_key: "azure-api-key-xyz",
          endpoint: "https://x.openai.azure.com",
          api_version: "2024-02-01",
        })
      )
    );
    // On success the modal closes → no secret survives in the DOM.
    await waitFor(() => expect(document.querySelector("input[type=password]")).toBeNull());
  });

  // ── R1 — incomplete body is rejected inline ────────────────────────────────
  it("test_R1_incomplete_422_inline", async () => {
    server.use(
      http.get(GW, () => HttpResponse.json({ keys: [] })),
      http.put(`${GW}/azure`, () =>
        HttpResponse.json(
          { code: "ERR_PROVIDER_CREDENTIAL_INCOMPLETE", title: "Incomplete or invalid credential for this provider", status: 422 },
          { status: 422 }
        )
      )
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure azure/i }));
    await user.click(await screen.findByRole("radio", { name: /aad|entra/i }));
    await user.type(screen.getByLabelText(/tenant id/i), "tid");
    await user.type(screen.getByLabelText(/client id/i), "cid");
    await user.type(screen.getByLabelText(/client secret/i), "sec");
    await user.type(screen.getByLabelText(/endpoint/i), "https://x.openai.azure.com");
    await user.type(screen.getByLabelText(/api version/i), "2024-02-01");
    await user.click(screen.getByRole("button", { name: /save/i }));

    // Inline error with the problem title; modal stays open (fields still present).
    await waitFor(() => expect(screen.getByText(/incomplete or invalid credential/i)).toBeInTheDocument());
    expect(screen.getByLabelText(/client secret/i)).toBeInTheDocument();
  });

  // ── R2 — empty required secret blocks the request ──────────────────────────
  it("test_R2_empty_secret_blocks", async () => {
    let putCount = 0;
    server.use(
      http.get(GW, () => HttpResponse.json({ keys: [] })),
      http.put(`${GW}/openai`, () => {
        putCount++;
        return HttpResponse.json(status("openai"));
      })
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure openai/i }));
    await screen.findByLabelText(/secret/i); // present, left blank
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(screen.getByText(/required/i)).toBeInTheDocument());
    // No PUT was made.
    expect(putCount).toBe(0);
  });

  // ── R3 — non-owner is denied ───────────────────────────────────────────────
  it("test_R3_forbidden_403", async () => {
    server.use(
      http.get(GW, () =>
        HttpResponse.json(
          { code: "ERR_AUTH_FORBIDDEN", title: "Owner role required", status: 403 },
          { status: 403 }
        )
      )
    );

    renderPanel();

    await waitFor(() => expect(screen.getByText(/owner role required/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /configure/i })).not.toBeInTheDocument();
  });

  // ── R4 — missing-encryption-key surfaces as a form error ───────────────────
  it("test_R4_encryption_unavailable_409", async () => {
    server.use(
      http.get(GW, () => HttpResponse.json({ keys: [] })),
      http.put(`${GW}/openai`, () =>
        HttpResponse.json(
          { code: "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE", title: "GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY must be set to store provider credentials", status: 409 },
          { status: 409 }
        )
      )
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure openai/i }));
    await user.type(await screen.findByLabelText(/secret/i), "sk-or-x");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(screen.getByText(/must be set to store provider credentials/i)).toBeInTheDocument());
    // Modal stays open; secret not cleared.
    expect((screen.getByLabelText(/secret/i) as HTMLInputElement).value).toBe("sk-or-x");
  });

  // ── R5 — a failed save preserves the entered secret for retry ──────────────
  it("test_R5_failed_save_keeps_secret", async () => {
    server.use(
      http.get(GW, () => HttpResponse.json({ keys: [] })),
      http.put(`${GW}/openai`, () =>
        HttpResponse.json({ code: "ERR_INTERNAL", title: "Internal server error", status: 500 }, { status: 500 })
      )
    );

    renderPanel();

    await user.click(await screen.findByRole("button", { name: /configure openai/i }));
    await user.type(await screen.findByLabelText(/secret/i), "sk-or-keepme");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(screen.getByText(/internal server error/i)).toBeInTheDocument());
    expect((screen.getByLabelText(/secret/i) as HTMLInputElement).value).toBe("sk-or-keepme");
  });

  // ── WIRING — the Provider Keys tab mounts the panel ────────────────────────
  it("test_WIRING_tab_registered", async () => {
    server.use(http.get(GW, () => HttpResponse.json({ keys: [status("openrouter")] })));

    render(<SettingsPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("tab", { name: /provider keys/i }));
    // The panel's GET fires on activation → all six providers render.
    await waitFor(() => expect(screen.getByText(/openrouter/i)).toBeInTheDocument());
    expect(screen.getByText(/bedrock/i)).toBeInTheDocument();
  });
});
