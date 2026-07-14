/**
 * tests-bff/govern-tier-capture.test.tsx — RED/GREEN for audit-remediation item 5:
 * KeyGovernanceEditor exposing `tier` and `capture_enabled`.
 *
 * Both fields are REAL, present-in-API fields on GET/PATCH /admin/keys/{id}
 * (apps/gateway/src/gateway/keys/api/schemas.py KeyInfoResponse: capture_enabled
 * bool default False; tier "priority"|"standard"|null, null = inherit tenant
 * default) but were missing from KeyGovernanceEditor's ApiKeyGovernance type AND
 * its rendered form — an owner/admin could never set/clear either from the
 * dashboard despite the gateway fully supporting it.
 *
 * Mirrors tests-bff/govern-depth.test.tsx's own conventions (teams GET stub,
 * dense-PATCH prefill "silent-clear guard" describe block) for the two new fields.
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { KeyGovernanceEditor } from "@/components/keys/KeyGovernanceEditor";
import { KeysPage } from "@/components/keys/KeysPage";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

const teamsGet = () => http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([]));

const KEY_BASE = {
  key_id: "00000000-0000-0000-0000-000000000001",
  name: "prod-key",
  prefix: "sk-1a2b3c",
  created_at: "2026-01-01T00:00:00Z",
  revoked_at: null,
  monthly_budget_usd: null,
  soft_budget_usd: null,
  expires_at: null,
  model_allowlist: null,
  rpm_limit: null,
  tpm_limit: null,
  team_id: null,
  cache_enabled: false,
  capture_enabled: false,
  tier: null as string | null,
};

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

describe("KeyGovernanceEditor — tier + capture_enabled (audit-remediation)", () => {
  it("test_tier_and_capture_controls_are_rendered", async () => {
    server.use(teamsGet());
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: /no team/i });

    expect(screen.getByRole("combobox", { name: /service tier/i })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /payload capture/i })).toBeInTheDocument();
  });

  it("test_set_tier_and_capture_saves_and_reflects", async () => {
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...KEY_BASE, tier: "priority", capture_enabled: true });
      }),
    );
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: /no team/i });

    await user.selectOptions(screen.getByRole("combobox", { name: /service tier/i }), "priority");
    await user.click(screen.getByRole("switch", { name: /payload capture/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.tier).toBe("priority");
    expect(captured!.capture_enabled).toBe(true);

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /service tier/i })).toHaveValue("priority"),
    );
    expect(screen.getByRole("switch", { name: /payload capture/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("test_clear_tier_reverts_to_tenant_default", async () => {
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    const keyWithTier = { ...KEY_BASE, tier: "priority" };
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...keyWithTier, tier: null });
      }),
    );
    render(<KeyGovernanceEditor apiKey={keyWithTier} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: /no team/i });

    expect(screen.getByRole("combobox", { name: /service tier/i })).toHaveValue("priority");
    await user.selectOptions(screen.getByRole("combobox", { name: /service tier/i }), "");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    // absent/null tier PATCH field = clear (revert to tenant default) — never omit
    // the field entirely, which the gateway would treat as "no change" (§3 PATCH
    // semantics: absent = no change; null = clear).
    expect(captured!.tier).toBeNull();
  });

  it("test_a_no_touch_save_round_trips_capture_enabled_as_a_boolean_never_null", async () => {
    // Dense-PATCH safety (mirrors govern-depth.test.tsx's cache_enabled precedent):
    // capture_enabled must always be sent as an explicit boolean, never dropped/null,
    // or a no-touch save would be a server no-op that silently fails to persist state.
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    const keyWithCapture = { ...KEY_BASE, capture_enabled: true };
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(keyWithCapture);
      }),
    );
    render(<KeyGovernanceEditor apiKey={keyWithCapture} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: /no team/i });

    expect(screen.getByRole("switch", { name: /payload capture/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.capture_enabled).toBe(true);
  });

  it("test_govern_tier_capture_axe_clean", async () => {
    server.use(teamsGet());
    const { container } = render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, {
      wrapper: Wrapper,
    });
    await screen.findByRole("option", { name: /no team/i });
    const { axe } = await import("@/test-support/axe");
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

/**
 * Silent-clear guard (mirrors govern-depth.test.tsx's own "prefill" describe block):
 * KeysPage's ApiKey/toGovernanceKey normalisation must carry tier + capture_enabled
 * from GET /admin/keys through to the editor, or a no-touch save would silently
 * clear a key's real tier override / disable its real capture setting.
 */
describe("KeysPage → governance editor prefill (tier/capture silent-clear guard)", () => {
  const KEY_WITH_TIER_AND_CAPTURE = { ...KEY_BASE, tier: "priority", capture_enabled: true };

  const keysListGet = () =>
    http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json([KEY_WITH_TIER_AND_CAPTURE]));

  it("test_list_fields_prefill_tier_and_capture", async () => {
    const user = userEvent.setup();
    server.use(keysListGet(), teamsGet());
    render(<KeysPage />, { wrapper: Wrapper });

    await screen.findByText("prod-key");
    await user.click(screen.getByRole("button", { name: /^governance$/i }));
    await screen.findByTestId("key-governance-editor");

    expect(screen.getByRole("combobox", { name: /service tier/i })).toHaveValue("priority");
    expect(screen.getByRole("switch", { name: /payload capture/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
