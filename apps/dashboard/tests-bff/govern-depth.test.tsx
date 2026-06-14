/**
 * tests-bff/govern-depth.test.tsx — RED suite for v15 governance-completion-ui (key editor depth).
 *
 * DEEPENS KeyGovernanceEditor with rpm_limit · tpm_limit · team_id (dropdown from
 * GET /admin/teams) · cache_enabled (Switch), all sent via the existing DENSE,
 * PREFILLED PATCH /admin/keys/{id}. The gateway already accepts every field.
 *
 * Dense-PATCH clear semantics (the §3 least-sure flag): rpm_limit/tpm_limit/team_id
 * present+null = CLEAR; a no-touch save must round-trip prefilled values unchanged.
 * cache_enabled present+null is a server no-op → the UI sends a BOOLEAN, never null.
 *
 * RED before build: the new controls/fields don't exist yet → these assertions fail
 * against the un-extended editor (the file imports fine; behavior is wrong).
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { axe } from "vitest-axe";
import { server } from "./mocks/server";
import React from "react";

import { KeyGovernanceEditor } from "@/components/keys/KeyGovernanceEditor";
import { KeysPage } from "@/components/keys/KeysPage";

const APP = "http://localhost:3000";

// KeyGovernanceEditor's team-assignment selector fires useQuery(["admin-teams"]) →
// GET /api/gw/admin/teams on every render. Register an EXPLICIT empty-list default
// for all tests (the BARE-array shape) now that the broad gw wildcard is gone; the
// few tests that exercise team assignment override it with their own server.use
// (msw prepends runtime handlers, so a later server.use wins). Strict-harness
// convention: scope shared fallbacks to the concrete paths a rendered component needs.
beforeEach(() => {
  server.use(
    http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json([]))
  );
});

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

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
};

const TEAMS = [
  {
    id: "team-alpha",
    name: "Alpha",
    tenant_id: "t1",
    created_at: "2026-01-01T00:00:00Z",
    member_count: 1,
    key_count: 0,
    team_budget_usd: null,
  },
  {
    id: "team-beta",
    name: "Beta",
    tenant_id: "t1",
    created_at: "2026-01-01T00:00:00Z",
    member_count: 2,
    key_count: 1,
    team_budget_usd: "100.00",
  },
];

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const teamsGet = () => http.get(`${APP}/api/gw/admin/teams`, () => HttpResponse.json(TEAMS));

describe("KeyGovernanceEditor — governance depth", () => {
  it("test_edit_rpm_tpm_team_cache_saves", async () => {
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...KEY_BASE,
          rpm_limit: 60,
          tpm_limit: 1000,
          team_id: "team-beta",
          cache_enabled: true,
        });
      }),
    );
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });

    // team options arrive from GET /admin/teams
    await screen.findByRole("option", { name: "Beta" });

    await user.type(screen.getByRole("textbox", { name: /requests per minute/i }), "60");
    await user.type(screen.getByRole("textbox", { name: /tokens per minute/i }), "1000");
    await user.selectOptions(screen.getByRole("combobox", { name: /team/i }), "team-beta");
    await user.click(screen.getByRole("switch", { name: /response cache/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.rpm_limit).toBe(60);
    expect(captured!.tpm_limit).toBe(1000);
    expect(captured!.team_id).toBe("team-beta");
    expect(captured!.cache_enabled).toBe(true);

    // persisted values are reflected back into the form (scenario "the persisted values are reflected")
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /requests per minute/i })).toHaveValue("60"),
    );
    expect(screen.getByRole("textbox", { name: /tokens per minute/i })).toHaveValue("1000");
    expect(screen.getByRole("combobox", { name: /team/i })).toHaveValue("team-beta");
    expect(screen.getByRole("switch", { name: /response cache/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("test_clear_rpm_tpm_and_unteam", async () => {
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    const keyFull = {
      ...KEY_BASE,
      rpm_limit: 30,
      tpm_limit: 500,
      team_id: "team-alpha",
      cache_enabled: false,
    };
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...keyFull, rpm_limit: null, tpm_limit: null, team_id: null });
      }),
    );
    render(<KeyGovernanceEditor apiKey={keyFull} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Alpha" });

    await user.clear(screen.getByRole("textbox", { name: /requests per minute/i }));
    await user.clear(screen.getByRole("textbox", { name: /tokens per minute/i }));
    await user.selectOptions(screen.getByRole("combobox", { name: /team/i }), "");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.rpm_limit).toBeNull();
    expect(captured!.tpm_limit).toBeNull();
    expect(captured!.team_id).toBeNull();
  });

  it("test_invalid_rate_limit_blocked", async () => {
    const user = userEvent.setup();
    let patchCalled = false;
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, () => {
        patchCalled = true;
        return HttpResponse.json(KEY_BASE);
      }),
    );
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Beta" });

    await user.type(screen.getByRole("textbox", { name: /requests per minute/i }), "0");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(patchCalled).toBe(false);
  });

  it("test_invalid_rate_limit_variants_blocked", async () => {
    // the reject list says "0, negative, or non-integer" — pin each form (rpm + tpm)
    let patchCalled = false;
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, () => {
        patchCalled = true;
        return HttpResponse.json(KEY_BASE);
      }),
    );

    for (const bad of ["-1", "abc", "1.5"]) {
      const user = userEvent.setup();
      const { unmount } = render(
        <KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />,
        { wrapper: Wrapper },
      );
      await screen.findByRole("option", { name: "Beta" });
      const rpm = screen.getByRole("textbox", { name: /requests per minute/i });
      await user.clear(rpm);
      await user.type(rpm, bad);
      await user.click(screen.getByRole("button", { name: /^save$/i }));
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      unmount();
    }

    // tpm shares the same rule — a 0 must also block (no PATCH for any variant)
    const user = userEvent.setup();
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Beta" });
    await user.type(screen.getByRole("textbox", { name: /tokens per minute/i }), "0");
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    expect(patchCalled).toBe(false);
  });

  it("test_server_422_rate_limit", async () => {
    const user = userEvent.setup();
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, () =>
        problem("Invalid rate limit", 422, "ERR_PAYLOAD_INVALID"),
      ),
    );
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Beta" });

    await user.type(screen.getByRole("textbox", { name: /requests per minute/i }), "60");
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid rate limit/i);
  });

  it("test_cross_tenant_team_404", async () => {
    const user = userEvent.setup();
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, () =>
        problem("Team not found", 404, "ERR_TEAM_NOT_FOUND"),
      ),
    );
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Beta" });

    await user.selectOptions(screen.getByRole("combobox", { name: /team/i }), "team-beta");
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/team not found/i);
  });

  it("test_cache_switch_reflects_and_sends_bool", async () => {
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    const keyFull = {
      ...KEY_BASE,
      rpm_limit: 30,
      tpm_limit: 500,
      team_id: "team-alpha",
      cache_enabled: true,
    };
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(keyFull);
      }),
    );
    render(<KeyGovernanceEditor apiKey={keyFull} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Alpha" });

    // cache switch reflects the loaded key (on)
    expect(screen.getByRole("switch", { name: /response cache/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    // a no-touch save round-trips the prefilled values unchanged (dense-PATCH safety)
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured!.cache_enabled).toBe(true); // boolean, never null
    expect(captured!.rpm_limit).toBe(30);
    expect(captured!.tpm_limit).toBe(500);
    expect(captured!.team_id).toBe("team-alpha");
  });

  it("test_member_403", async () => {
    const user = userEvent.setup();
    server.use(
      teamsGet(),
      http.patch(`${APP}/api/gw/admin/keys/:id`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    await screen.findByRole("option", { name: "Beta" });

    await user.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/forbidden/i);
  });

  it("test_team_dropdown_from_admin_teams", async () => {
    server.use(teamsGet());
    render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, { wrapper: Wrapper });
    expect(await screen.findByRole("option", { name: "Alpha" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Beta" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /no team/i })).toBeInTheDocument();
  });

  it("test_govern_depth_axe_clean", async () => {
    server.use(teamsGet());
    const { container } = render(<KeyGovernanceEditor apiKey={KEY_BASE} onUpdated={() => {}} />, {
      wrapper: Wrapper,
    });
    await screen.findByRole("option", { name: "Beta" });
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

/**
 * KeysPage → KeyGovernanceEditor wiring. The editor's dense PATCH prefill (the §3
 * #1 least-sure flag) is only safe if KeysPage hands the 4 governance depth fields
 * from GET /admin/keys down to the editor. If the list→editor normalisation drops
 * them, the editor prefills blank/false and a no-touch save SILENTLY CLEARS the
 * key's real rpm/tpm/team and disables its cache. This pins that handoff.
 */
describe("KeysPage → governance editor prefill (silent-clear guard)", () => {
  const KEY_WITH_GOVERNANCE = {
    key_id: "00000000-0000-0000-0000-0000000000aa",
    name: "prod-key",
    prefix: "sk-1a2b3c",
    created_at: "2026-01-01T00:00:00Z",
    revoked_at: null,
    monthly_budget_usd: null,
    soft_budget_usd: null,
    expires_at: null,
    model_allowlist: null,
    rpm_limit: 60,
    tpm_limit: 1000,
    team_id: "team-beta",
    cache_enabled: true,
  };

  const keysListGet = () =>
    http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json([KEY_WITH_GOVERNANCE]));

  it("test_list_fields_prefill_the_editor", async () => {
    const user = userEvent.setup();
    server.use(keysListGet(), teamsGet());
    render(<KeysPage />, { wrapper: Wrapper });

    // key row arrives from GET /admin/keys
    await screen.findByText("prod-key");

    // expand the governance editor for that key
    await user.click(screen.getByRole("button", { name: /^governance$/i }));
    await screen.findByTestId("key-governance-editor");

    // the editor must reflect the key's REAL governance values (not blank/false)
    expect(screen.getByRole("textbox", { name: /requests per minute/i })).toHaveValue("60");
    expect(screen.getByRole("textbox", { name: /tokens per minute/i })).toHaveValue("1000");
    await screen.findByRole("option", { name: "Beta" });
    expect(screen.getByRole("combobox", { name: /team/i })).toHaveValue("team-beta");
    expect(screen.getByRole("switch", { name: /response cache/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
