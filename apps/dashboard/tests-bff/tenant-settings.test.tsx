/**
 * tests-bff/tenant-settings.test.tsx — RED suite for v15 task tenant-settings-ui.
 *
 * A NEW owner/admin `/settings` tabbed hub (Cache · Guardrails · SSO) over THREE
 * frozen gateway contracts via the BFF seam (bffGet/bffPut, verbatim JSON):
 *   GET/PUT /admin/cache       { enabled, semantic_enabled }            (owner/admin write)
 *   GET/PUT /admin/guardrails  { prompt_injection, pii_mask(+patterns) }(owner/admin write)
 *   GET/PUT /admin/oidc        OWNER-only; client_secret is WRITE-ONLY (GET → "<stored>")
 *
 * Each tab fetches its own data lazily (TabsContent unmounts when inactive), so
 * the default Cache tab's GET always fires on mount; switching tabs fires that
 * tab's GET. SECURITY: the "<stored>" sentinel must NEVER appear in any input.
 *
 * RED before build: `@/components/settings/SettingsPage` does not exist → the file
 * fails at collect (MODULE_NOT_FOUND), the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse, delay } from "msw";
import { axe } from "@/test-support/axe";
import { server } from "./mocks/server";
import React from "react";

// ── RED: import fails until Build writes components/settings/SettingsPage.tsx ────
import { SettingsPage } from "@/components/settings/SettingsPage";
import { AppShell } from "@/components/ui";

const APP = "http://localhost:3000";

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

const CACHE_ON = { enabled: true, semantic_enabled: false };
const GUARD = { prompt_injection: { enabled: true, mode: "block" }, pii_mask: null };
const OIDC = {
  tenant_id: "t1",
  issuer: "https://idp.example.com",
  client_id: "cid-123",
  client_secret: "<stored>",
  authorize_url: "https://idp.example.com/auth",
  token_url: "https://idp.example.com/token",
  jwks_url: "https://idp.example.com/jwks",
  email_domains: ["acme.io"],
  enabled: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const SCIM_TOKEN_ACTIVE = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Okta",
  created_at: "2026-01-01T00:00:00Z",
  revoked_at: null as string | null,
};

const SCIM_CREATE_RESP = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Okta",
  token: "scim_plaintext_created_abc123",
  created_at: "2026-01-02T00:00:00Z",
};

const SCIM_ROTATE_RESP = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Okta",
  token: "scim_plaintext_rotated_xyz789",
  created_at: "2026-01-03T00:00:00Z",
};

const SAML_CONF = {
  tenant_id: "t1",
  idp_entity_id: "https://idp.example.com/entity",
  idp_sso_url: "https://idp.example.com/sso",
  idp_x509_cert: "-----BEGIN CERTIFICATE-----\nMIIB0zCCA=\n-----END CERTIFICATE-----",
  sp_entity_id: "https://gw.example.com/saml/t1",
  acs_url: "https://gw.example.com/saml/acs",
  email_domains: ["acme.io"],
  email_attribute_name: "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
  enabled: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const RETENTION_DEFAULT = {
  window_days: null as number | null,
  effective_window_days: {
    usage_records: 90,
    alert_events: 90,
    artifacts: 90,
    conversations: 90,
    memories: 90,
    batch_job_items: 90,
    video_generation_jobs: 90,
  },
  zdr_enabled: false,
  zdr_enabled_at: null as string | null,
  operator_ceiling_days: 365,
};

const RETENTION_ZDR_ON = {
  ...RETENTION_DEFAULT,
  zdr_enabled: true,
  zdr_enabled_at: "2026-07-10T00:00:00Z",
};

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const cacheGet = (body: unknown = CACHE_ON) =>
  http.get(`${APP}/api/gw/admin/cache`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

const scimGet = (body: unknown = { tokens: [] }) =>
  http.get(`${APP}/api/gw/admin/scim/tokens`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

const samlGet = (body: unknown = SAML_CONF) =>
  http.get(`${APP}/api/gw/admin/saml`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

const retentionGet = (body: unknown = RETENTION_DEFAULT) =>
  http.get(`${APP}/api/gw/admin/retention-policy`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

// residency-tiers-ui TASK.md §3 (residency-policy §3 FROZEN @ v2, CR-1 — region enum
// is {null,"us","eu","ap"}). A default {region:null,...} is ALSO registered as an
// INITIAL handler in mocks/handlers.ts (DataResidencyFieldset's GET now fires
// unconditionally whenever this tab mounts) — tests below override per scenario.
const RESIDENCY_UNSET = { region: null as string | null, updated_at: null as string | null };
const RESIDENCY_US = { region: "us", updated_at: "2026-07-01T00:00:00Z" };
const RESIDENCY_EU = { region: "eu", updated_at: "2026-07-05T00:00:00Z" };

const residencyGet = (body: unknown = RESIDENCY_UNSET) =>
  http.get(`${APP}/api/gw/admin/residency-policy`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

async function openTab(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  await screen.findByRole("tablist");
  await user.click(screen.getByRole("tab", { name }));
}

// ── shell + tabs ──────────────────────────────────────────────────────────────
describe("SettingsPage — tab shell", () => {
  it("test_renders_three_tabs_cache_default", async () => {
    server.use(cacheGet());
    render(<SettingsPage />, { wrapper: Wrapper });

    const tablist = await screen.findByRole("tablist");
    expect(within(tablist).getByRole("tab", { name: /cache/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /guardrails/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /^sso$/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /cache/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    expect(await screen.findByRole("switch", { name: /response cache/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: /semantic cache/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("test_tabs_keyboard_roving", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/guardrails`, () => HttpResponse.json(GUARD)),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    const cacheTab = await screen.findByRole("tab", { name: /cache/i });
    cacheTab.focus();
    await user.keyboard("{ArrowRight}");

    expect(screen.getByRole("tab", { name: /guardrails/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

// ── cache tab ──────────────────────────────────────────────────────────────────
describe("SettingsPage — cache tab", () => {
  it("test_save_cache", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      http.put(`${APP}/api/gw/admin/cache`, async ({ request }) => {
        await delay(40);
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ enabled: false, semantic_enabled: true });
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /response cache/i });

    await user.click(screen.getByRole("switch", { name: /response cache/i }));
    await user.click(screen.getByRole("switch", { name: /semantic cache/i }));
    const save = screen.getByRole("button", { name: /save/i });
    await user.click(save);

    await waitFor(() => expect(save).toBeDisabled());
    await waitFor(() => expect(putBody).toEqual({ enabled: false, semantic_enabled: true }));
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /response cache/i })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
    // A successful save surfaces an explicit confirmation (no more silent success).
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("test_member_cannot_save_cache", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.put(`${APP}/api/gw/admin/cache`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /response cache/i });

    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/forbidden/i);
  });

  it("test_tab_get_failure_shows_alert", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/cache`, () => problem("Server error", 500, "ERR_INTERNAL")),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

// ── guardrails tab ───────────────────────────────────────────────────────────────
describe("SettingsPage — guardrails tab", () => {
  async function openGuardrails(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole("tablist");
    await user.click(screen.getByRole("tab", { name: /guardrails/i }));
  }

  it("test_view_and_save_guardrails", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/guardrails`, () => HttpResponse.json(GUARD)),
      http.put(`${APP}/api/gw/admin/guardrails`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(GUARD);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openGuardrails(user);

    expect(await screen.findByRole("switch", { name: /prompt injection/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: /pii/i })).toHaveAttribute("aria-checked", "false");

    // add a custom pattern under pii then save
    await user.click(screen.getByRole("switch", { name: /pii/i }));
    await user.click(screen.getByRole("button", { name: /add pattern/i }));
    await user.type(screen.getByRole("textbox", { name: /pattern name 1/i }), "SSN");
    // NOTE: avoid `{}` in the typed value — userEvent v14 treats `{...}` as a key
    // descriptor, so a brace-free regex round-trips cleanly through the input.
    await user.type(screen.getByRole("textbox", { name: /pattern regex 1/i }), "\\d+");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(putBody).not.toBeNull());
    const pii = (putBody as unknown as Record<string, unknown>).pii_mask as Record<string, unknown>;
    expect(pii.pii_custom_patterns).toEqual([{ name: "SSN", pattern: "\\d+" }]);
  });

  it("test_guardrails_invalid_pattern_422", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/guardrails`, () => HttpResponse.json(GUARD)),
      http.put(`${APP}/api/gw/admin/guardrails`, () =>
        problem("Custom pattern validation failed", 422, "ERR_PAYLOAD_INVALID"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openGuardrails(user);
    await screen.findByRole("switch", { name: /prompt injection/i });

    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/validation failed/i);
  });
});

// ── SSO tab (owner-only, write-only secret) ──────────────────────────────────────
describe("SettingsPage — SSO tab", () => {
  async function openSso(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole("tablist");
    await user.click(screen.getByRole("tab", { name: /^sso$/i }));
  }

  it("test_owner_views_sso_no_secret", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () => HttpResponse.json(OIDC)),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);

    expect(await screen.findByDisplayValue("https://idp.example.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("cid-123")).toBeInTheDocument();
    // SECURITY: client_secret input is empty; the "<stored>" sentinel appears in NO input
    const secret = screen.getByLabelText(/client secret/i);
    expect(secret).toHaveValue("");
    expect(screen.queryByDisplayValue("<stored>")).not.toBeInTheDocument();
    // an informational note tells the user a secret is stored
    expect(screen.getByText(/secret is stored/i)).toBeInTheDocument();
  });

  it("test_sso_first_time_404_empty_form", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () =>
        problem("Not configured", 404, "ERR_OIDC_CONFIG_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);

    // editable empty form, NOT an error
    const issuer = await screen.findByLabelText(/issuer/i);
    expect(issuer).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_admin_forbidden_sso", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () =>
        problem("Owner role required", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/owner role required/i);
    expect(screen.queryByLabelText(/issuer/i)).not.toBeInTheDocument();
    // SECURITY: an admin's 403 renders NO form — the client_secret field must not
    // exist at all, and no textbox should be present (ErrorState only).
    expect(screen.queryByLabelText(/client secret/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("test_save_sso", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () => HttpResponse.json(OIDC)),
      http.put(`${APP}/api/gw/admin/oidc`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(OIDC);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);
    await screen.findByDisplayValue("https://idp.example.com");

    await user.type(screen.getByLabelText(/client secret/i), "freshsecret");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect((putBody as unknown as Record<string, unknown>).client_secret).toBe("freshsecret");
    expect((putBody as unknown as Record<string, unknown>).issuer).toBe("https://idp.example.com");
    // SECURITY: after a successful save the secret input is cleared (write-only UX)
    // and the "<stored>" sentinel from the refetched GET never lands in any input.
    await waitFor(() => expect(screen.getByLabelText(/client secret/i)).toHaveValue(""));
    expect(screen.queryByDisplayValue("<stored>")).not.toBeInTheDocument();
  });

  it("test_sso_empty_secret_blocked", async () => {
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () => HttpResponse.json(OIDC)),
      http.put(`${APP}/api/gw/admin/oidc`, () => {
        putCalled = true;
        return HttpResponse.json(OIDC);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);
    await screen.findByDisplayValue("https://idp.example.com");

    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(putCalled).toBe(false);
  });

  it("test_sso_bad_url_422", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () => HttpResponse.json(OIDC)),
      http.put(`${APP}/api/gw/admin/oidc`, () =>
        problem("Invalid URL", 422, "ERR_PAYLOAD_INVALID"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);
    await screen.findByDisplayValue("https://idp.example.com");

    await user.type(screen.getByLabelText(/client secret/i), "freshsecret");
    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid url/i);
  });

  it("test_sso_encryption_409", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/oidc`, () => HttpResponse.json(OIDC)),
      http.put(`${APP}/api/gw/admin/oidc`, () =>
        problem("Encryption not configured", 409, "ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openSso(user);
    await screen.findByDisplayValue("https://idp.example.com");

    await user.type(screen.getByLabelText(/client secret/i), "freshsecret");
    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/encryption not configured/i);
  });
});

// ── new tabs: shell / lazy-load (enterprise-identity-admin-ui) ────────────────────
describe("SettingsPage — new tabs shell", () => {
  it("test_renders_seven_tabs_no_premature_fetch", async () => {
    let scimCount = 0;
    let samlCount = 0;
    let retentionCount = 0;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/scim/tokens`, () => {
        scimCount += 1;
        return HttpResponse.json({ tokens: [] });
      }),
      http.get(`${APP}/api/gw/admin/saml`, () => {
        samlCount += 1;
        return HttpResponse.json(SAML_CONF);
      }),
      http.get(`${APP}/api/gw/admin/retention-policy`, () => {
        retentionCount += 1;
        return HttpResponse.json(RETENTION_DEFAULT);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });

    const tablist = await screen.findByRole("tablist");
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Cache",
      "Guardrails",
      "SSO",
      "Provider Keys",
      "SCIM",
      "SAML SSO",
      "Data & residency",
    ]);

    // Cache's GET fired (default tab); the three new tabs' GETs must NOT have fired yet.
    await screen.findByRole("switch", { name: /response cache/i });
    expect(scimCount).toBe(0);
    expect(samlCount).toBe(0);
    expect(retentionCount).toBe(0);
  });

  it("test_scim_tab_lazy_fetches_once", async () => {
    const user = userEvent.setup();
    let scimCount = 0;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/scim/tokens`, () => {
        scimCount += 1;
        return HttpResponse.json({ tokens: [] });
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /response cache/i });

    await openTab(user, /^scim$/i);
    await screen.findByText(/no scim tokens yet/i);
    expect(scimCount).toBe(1);

    // Re-clicking away and back: TanStack Query serves the cached data
    // instantly (no loading spinner — the empty-state text is already there
    // synchronously) and revalidates in the background, EXACTLY matching the
    // real, empirically-confirmed behavior of the existing Guardrails/Cache/SSO
    // tabs (none of which set staleTime, so all refetch on every remount — a
    // second network call here is the byte-identical M2 outcome, not a
    // regression; no staleTime override is introduced for SCIM either).
    await openTab(user, /^cache$/i);
    await openTab(user, /^scim$/i);
    await screen.findByText(/no scim tokens yet/i);
    expect(scimCount).toBe(2);
  });
});

// ── SCIM tab ────────────────────────────────────────────────────────────────────
describe("SettingsPage — SCIM tab", () => {
  it("test_owner_creates_scim_token_reveals_once", async () => {
    const user = userEvent.setup();
    let postBody: Record<string, unknown> | null = null;
    let listCallCount = 0;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/scim/tokens`, () => {
        listCallCount += 1;
        return HttpResponse.json(listCallCount === 1 ? { tokens: [] } : { tokens: [SCIM_TOKEN_ACTIVE] });
      }),
      http.post(`${APP}/api/gw/admin/scim/tokens`, async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(SCIM_CREATE_RESP, { status: 201 });
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText(/no scim tokens yet/i);

    await user.click(screen.getByRole("button", { name: /create token/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/key name/i), "Okta");
    await user.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(postBody).toEqual({ name: "Okta" }));
    const banner = await screen.findByRole("alert");
    expect(within(banner).getByText(SCIM_CREATE_RESP.token)).toBeInTheDocument();

    // list re-fetches and shows the new row; no plaintext visible in the row itself.
    await screen.findByText("Okta");
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.queryByText(SCIM_CREATE_RESP.token, { selector: "td" })).not.toBeInTheDocument();

    // dismissing the banner clears the plaintext from component state.
    await user.click(within(banner).getByRole("button", { name: /done/i }));
    expect(screen.queryByText(SCIM_CREATE_RESP.token)).not.toBeInTheDocument();
  });

  it("test_member_cannot_see_scim_affordances", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/scim/tokens`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/forbidden/i);
    expect(screen.queryByRole("button", { name: /create token/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /rotate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /revoke/i })).not.toBeInTheDocument();
  });

  it("test_scim_create_name_validation_blocks_client_side", async () => {
    const user = userEvent.setup();
    let postCalled = false;
    server.use(
      cacheGet(),
      scimGet(),
      http.post(`${APP}/api/gw/admin/scim/tokens`, () => {
        postCalled = true;
        return HttpResponse.json(SCIM_CREATE_RESP, { status: 201 });
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText(/no scim tokens yet/i);

    await user.click(screen.getByRole("button", { name: /create token/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/key name is required/i);
    expect(postCalled).toBe(false);
  });

  it("test_scim_rotate_reveals_new_plaintext", async () => {
    const user = userEvent.setup();
    let rotateBody: unknown = undefined;
    server.use(
      cacheGet(),
      scimGet({ tokens: [SCIM_TOKEN_ACTIVE] }),
      http.post(`${APP}/api/gw/admin/scim/tokens/:id/rotate`, async ({ request }) => {
        rotateBody = await request.text();
        return HttpResponse.json(SCIM_ROTATE_RESP);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText("Okta");

    await user.click(screen.getByRole("button", { name: /rotate okta/i }));
    const confirmDialog = await screen.findByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: /^rotate$/i }));

    await waitFor(() => expect(rotateBody).toBeDefined());
    const banner = await screen.findByRole("alert");
    expect(within(banner).getByText(SCIM_ROTATE_RESP.token)).toBeInTheDocument();
  });

  it("test_scim_revoke_shows_destructive_badge", async () => {
    const user = userEvent.setup();
    let deleteCalled = false;
    let listCallCount = 0;
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/scim/tokens`, () => {
        listCallCount += 1;
        return HttpResponse.json(
          listCallCount === 1
            ? { tokens: [SCIM_TOKEN_ACTIVE] }
            : { tokens: [{ ...SCIM_TOKEN_ACTIVE, revoked_at: "2026-01-05T00:00:00Z" }] },
        );
      }),
      http.delete(`${APP}/api/gw/admin/scim/tokens/:id`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText("Okta");

    await user.click(screen.getByRole("button", { name: /revoke okta/i }));
    const confirmDialog = await screen.findByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: /^revoke$/i }));

    await waitFor(() => expect(deleteCalled).toBe(true));
    expect(await screen.findByText(/revoked/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /rotate okta/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /revoke okta/i })).not.toBeInTheDocument();
  });

  it("test_scim_revoke_404_race_stays_open_inline", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      scimGet({ tokens: [SCIM_TOKEN_ACTIVE] }),
      http.delete(`${APP}/api/gw/admin/scim/tokens/:id`, () =>
        problem("Token not found", 404, "ERR_SCIM_TOKEN_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText("Okta");

    await user.click(screen.getByRole("button", { name: /revoke okta/i }));
    const confirmDialog = await screen.findByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: /^revoke$/i }));

    expect(await within(confirmDialog).findByRole("alert")).toHaveTextContent(/not found/i);
    // dialog stays open — did not silently close as if it succeeded.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("test_scim_rotate_404_race_stays_open_inline", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      scimGet({ tokens: [SCIM_TOKEN_ACTIVE] }),
      http.post(`${APP}/api/gw/admin/scim/tokens/:id/rotate`, () =>
        problem("Token not found", 404, "ERR_SCIM_TOKEN_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText("Okta");

    await user.click(screen.getByRole("button", { name: /rotate okta/i }));
    const confirmDialog = await screen.findByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: /^rotate$/i }));

    expect(await within(confirmDialog).findByRole("alert")).toHaveTextContent(/not found/i);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText(SCIM_ROTATE_RESP.token)).not.toBeInTheDocument();
  });

  it("test_scim_create_403_mid_session_stays_open", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      scimGet(),
      http.post(`${APP}/api/gw/admin/scim/tokens`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);
    await screen.findByText(/no scim tokens yet/i);

    await user.click(screen.getByRole("button", { name: /create token/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/key name/i), "Okta");
    await user.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/forbidden/i);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // no plaintext banner, no token listed — the create never succeeded.
    expect(screen.queryByText(SCIM_CREATE_RESP.token)).not.toBeInTheDocument();
  });
});

// ── SAML SSO tab ──────────────────────────────────────────────────────────────────
describe("SettingsPage — SAML SSO tab", () => {
  it("test_saml_tab_first_time_empty_form", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/saml`, () =>
        problem("Not configured", 404, "ERR_SAML_CONFIG_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^saml sso$/i);

    const entityId = await screen.findByLabelText(/idp entity id/i);
    expect(entityId).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/sp entity id/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/acs url/i)).not.toBeInTheDocument();
  });

  it("test_saml_tab_prefills_cert_unlike_oidc_secret", async () => {
    const user = userEvent.setup();
    server.use(cacheGet(), samlGet());
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^saml sso$/i);

    // Multi-line PEM text defeats getByDisplayValue's whitespace-collapsing
    // normalizer (it treats embedded newlines as collapsible whitespace on the
    // query string but not consistently against the raw textarea .value) — assert
    // the property directly instead of relying on the TextMatch normalizer.
    const certField = (await screen.findByLabelText(
      /idp x509 certificate/i,
    )) as HTMLTextAreaElement;
    expect(certField.value).toBe(SAML_CONF.idp_x509_cert);
    expect(screen.getByText(SAML_CONF.sp_entity_id)).toBeInTheDocument();
    expect(screen.getByText(SAML_CONF.acs_url)).toBeInTheDocument();
  });

  it("test_saml_put_invalid_cert_422_preserves_form", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      samlGet(),
      http.put(`${APP}/api/gw/admin/saml`, () =>
        problem("SAML IdP certificate is invalid or expired", 422, "ERR_SAML_CERT_INVALID"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^saml sso$/i);
    await screen.findByLabelText(/idp x509 certificate/i);

    const certField = screen.getByLabelText(/idp x509 certificate/i);
    await user.clear(certField);
    await user.type(certField, "not a cert");
    await user.click(screen.getByRole("button", { name: /save/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/certificate is invalid/i);
    expect(certField).toHaveValue("not a cert");
  });

  it("test_saml_put_bad_url_422_preserves_form", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      samlGet(),
      http.put(`${APP}/api/gw/admin/saml`, () =>
        HttpResponse.json(
          {
            detail: [
              {
                type: "url_scheme_invalid",
                loc: ["body", "idp_sso_url"],
                msg: "idp_sso_url: RFC-1918/loopback IP addresses are not permitted in production",
                input: "http://192.168.1.1/sso",
              },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^saml sso$/i);
    await screen.findByLabelText(/idp x509 certificate/i);

    const urlField = screen.getByLabelText(/idp sso url/i);
    await user.clear(urlField);
    await user.type(urlField, "http://192.168.1.1/sso");
    await user.click(screen.getByRole("button", { name: /save/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not permitted in production/i);
    expect(urlField).toHaveValue("http://192.168.1.1/sso");
  });

  it("test_saml_non_owner_no_form", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/saml`, () =>
        problem("Owner role required", 403, "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^saml sso$/i);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/owner role required/i);
    expect(screen.queryByLabelText(/idp entity id/i)).not.toBeInTheDocument();
  });
});

// ── Retention & ZDR tab ─────────────────────────────────────────────────────────────
describe("SettingsPage — Retention & ZDR tab (now labeled Data & residency)", () => {
  it("test_retention_tab_default_state_seven_rows_no_audit", async () => {
    const user = userEvent.setup();
    server.use(cacheGet(), retentionGet());
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);

    const windowInput = await screen.findByLabelText(/window \(days\)/i);
    expect(windowInput).toHaveValue(null);
    expect(screen.getByRole("switch", { name: /enable zero-data-retention/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByText(/inherits operator default/i)).toBeInTheDocument();
    expect(screen.getByText(/operator ceiling: 365 days/i)).toBeInTheDocument();

    for (const label of [
      "Usage records",
      "Alert events",
      "Artifacts",
      "Conversations",
      "Memories",
      "Batch job items",
      "Video generation jobs",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText(/audit_events/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Audit events$/i)).not.toBeInTheDocument();
  });

  it("test_retention_put_shortens_window", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      retentionGet(),
      http.put(`${APP}/api/gw/admin/retention-policy`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        const shortened = {
          ...RETENTION_DEFAULT,
          window_days: 30,
          effective_window_days: Object.fromEntries(
            Object.keys(RETENTION_DEFAULT.effective_window_days).map((k) => [k, 30]),
          ),
        };
        return HttpResponse.json(shortened);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    const windowInput = await screen.findByLabelText(/window \(days\)/i);

    await user.clear(windowInput);
    await user.type(windowInput, "30");
    await user.click(screen.getByRole("button", { name: /save window/i }));

    await waitFor(() => expect(putBody).toEqual({ window_days: 30 }));
    await waitFor(() => expect(windowInput).toHaveValue(30));
    const rows = screen.getAllByText("30");
    expect(rows.length).toBeGreaterThanOrEqual(7);
  });

  it("test_retention_window_out_of_bounds_422_inline", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      retentionGet(),
      http.put(`${APP}/api/gw/admin/retention-policy`, () =>
        problem("Window exceeds operator ceiling", 422, "ERR_RETENTION_WINDOW_INVALID"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    const windowInput = await screen.findByLabelText(/window \(days\)/i);

    await user.clear(windowInput);
    await user.type(windowInput, "400");
    await user.click(screen.getByRole("button", { name: /save window/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/exceeds operator ceiling/i);
    expect(windowInput).toHaveValue(400);
  });

  it("test_zdr_enable_requires_confirm_dialog", async () => {
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      cacheGet(),
      retentionGet(),
      http.put(`${APP}/api/gw/admin/retention-policy`, () => {
        putCalled = true;
        return HttpResponse.json(RETENTION_ZDR_ON);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByLabelText(/window \(days\)/i);

    const zdrSwitch = screen.getByRole("switch", { name: /enable zero-data-retention/i });
    await user.click(zdrSwitch);

    const confirmDialog = await screen.findByRole("dialog");
    expect(putCalled).toBe(false);

    await user.click(within(confirmDialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(zdrSwitch).toHaveAttribute("aria-checked", "false");
    expect(putCalled).toBe(false);
  });

  it("test_zdr_switch_disabled_while_confirm_open", async () => {
    // Defense-in-depth: while the enable-confirm dialog is open the switch is optimistically
    // `true`; a forced/synthetic click would otherwise fire PUT{zdr_enabled:false} past the
    // open dialog. The switch must be component-level disabled, not merely overlay-blocked.
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      cacheGet(),
      retentionGet(),
      http.put(`${APP}/api/gw/admin/retention-policy`, () => {
        putCalled = true;
        return HttpResponse.json(RETENTION_ZDR_ON);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByLabelText(/window \(days\)/i);

    const zdrSwitch = screen.getByRole("switch", { name: /enable zero-data-retention/i });
    await user.click(zdrSwitch);
    await screen.findByRole("dialog");

    expect(zdrSwitch).toBeDisabled();
    expect(putCalled).toBe(false);
  });

  it("test_zdr_enable_confirm_fires_put", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      retentionGet(),
      http.put(`${APP}/api/gw/admin/retention-policy`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(RETENTION_ZDR_ON);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByLabelText(/window \(days\)/i);

    const zdrSwitch = screen.getByRole("switch", { name: /enable zero-data-retention/i });
    await user.click(zdrSwitch);
    const confirmDialog = await screen.findByRole("dialog");
    await user.click(within(confirmDialog).getByRole("button", { name: /enable zdr/i }));

    await waitFor(() => expect(putBody).toEqual({ zdr_enabled: true }));
    await waitFor(() => expect(zdrSwitch).toHaveAttribute("aria-checked", "true"));
  });

  it("test_zdr_disable_no_confirm_dialog", async () => {
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      retentionGet(RETENTION_ZDR_ON),
      http.put(`${APP}/api/gw/admin/retention-policy`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(RETENTION_DEFAULT);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    const zdrSwitch = await screen.findByRole("switch", { name: /enable zero-data-retention/i });
    expect(zdrSwitch).toHaveAttribute("aria-checked", "true");

    await user.click(zdrSwitch);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(putBody).toEqual({ zdr_enabled: false }));
  });

  it("test_zdr_active_deemphasizes_not_hides_window", async () => {
    const user = userEvent.setup();
    server.use(cacheGet(), retentionGet(RETENTION_ZDR_ON));
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);

    const windowInput = await screen.findByLabelText(/window \(days\)/i);
    expect(windowInput).toBeDisabled();
    // still present, not removed — the effective-window table remains fully visible.
    expect(screen.getByText("Usage records")).toBeInTheDocument();
    expect(screen.getAllByText("90").length).toBeGreaterThanOrEqual(7);
  });

  it("test_retention_non_owner_no_form", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/retention-policy`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/forbidden/i);
    expect(screen.queryByLabelText(/window \(days\)/i)).not.toBeInTheDocument();
  });
});

// ── Data residency fieldset (residency-tiers-ui TASK.md §3 M1-M5, R1-R3) ───────────
//
// NOTE (CR-1, "DECIDED at freeze review", residency-tiers-ui TASK.md §3): the ORIGINAL
// v1 draft scope-cut "ap" as a visibly-disabled option (Issue #1). That fork was
// SUPERSEDED before this task's own freeze — residency-policy re-froze @ v2 with "ap"
// in its region enum, so this v1 build ships "ap" as a FULLY ENABLED pin choice, with
// its own consequence-line copy joining EU/US. The superseded "AP disabled" scenario
// text in §2 is historical (frozen, never edited); this suite tests the CURRENT,
// CR-1-corrected behavior per the binding freeze-review note.
const EU_CONSEQUENCE =
  "Pinning to EU means requests that cannot run in the EU will be refused, not rerouted. This also blocks realtime voice for this tenant — no realtime model is region-tagged yet.";
const US_CONSEQUENCE =
  "Pinning to US means requests that cannot run in the US will be refused, not rerouted. This also blocks realtime voice for this tenant — no realtime model is region-tagged yet.";

describe("SettingsPage — Data residency fieldset", () => {
  it("test_third_fieldset_renders_below_retention_and_zdr", async () => {
    // M1: retention + ZDR fieldsets render unchanged; a new "Data residency" fieldset
    // renders below them, fetching GET /admin/residency-policy independently.
    const user = userEvent.setup();
    server.use(cacheGet(), retentionGet(), residencyGet());
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);

    await screen.findByLabelText(/window \(days\)/i);
    expect(screen.getByRole("switch", { name: /enable zero-data-retention/i })).toBeInTheDocument();
    expect(await screen.findByText(/data residency/i)).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: /no pin \(unrestricted\)/i })).toBeChecked();
  });

  it("test_fresh_pin_eu_shows_confirm_dialog_before_put", async () => {
    // M3: unset -> EU triggers ConfirmDialog with the EU consequence line, PUT NOT yet called.
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_UNSET),
      http.put(`${APP}/api/gw/admin/residency-policy`, () => {
        putCalled = true;
        return HttpResponse.json(RESIDENCY_EU);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^eu$/i });

    await user.click(screen.getByRole("radio", { name: /^eu$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(EU_CONSEQUENCE)).toBeInTheDocument();
    expect(putCalled).toBe(false);
  });

  it("test_switching_existing_pin_us_to_eu_also_confirms", async () => {
    // M3: pinned A -> pinned B is NOT exempt from the confirm gate.
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_US),
      http.put(`${APP}/api/gw/admin/residency-policy`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(RESIDENCY_EU);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    const usRadio = await screen.findByRole("radio", { name: /^us$/i });
    expect(usRadio).toBeChecked();

    await user.click(screen.getByRole("radio", { name: /^eu$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /pin to eu/i }));

    await waitFor(() => expect(putBody).toEqual({ region: "eu" }));
  });

  it("test_confirming_eu_pin_persists_and_reconciles_from_response", async () => {
    // M3: on success the fieldset displays the PUT response value, not local state.
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_UNSET),
      http.put(`${APP}/api/gw/admin/residency-policy`, () => HttpResponse.json(RESIDENCY_EU)),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^eu$/i });

    await user.click(screen.getByRole("radio", { name: /^eu$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /pin to eu/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("radio", { name: /^eu$/i })).toBeChecked();
  });

  it("test_cancelling_confirm_leaves_server_pin_unchanged", async () => {
    // M3, mirrors handleZdrConfirmClose: cancel -> no PUT, reconciles to the last-known
    // server value ("US"), not the pending "EU" selection.
    const user = userEvent.setup();
    let putCalled = false;
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_US),
      http.put(`${APP}/api/gw/admin/residency-policy`, () => {
        putCalled = true;
        return HttpResponse.json(RESIDENCY_EU);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^us$/i });

    await user.click(screen.getByRole("radio", { name: /^eu$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("radio", { name: /^us$/i })).toBeChecked();
    expect(putCalled).toBe(false);
  });

  it("test_clearing_pin_fires_immediately_no_confirm", async () => {
    // M4: loosening (pin -> unrestricted) is safe/immediate, mirrors ZDR-disable.
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_EU),
      http.put(`${APP}/api/gw/admin/residency-policy`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(RESIDENCY_UNSET);
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^eu$/i });

    await user.click(screen.getByRole("radio", { name: /no pin \(unrestricted\)/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(putBody).toEqual({ region: null }));
  });

  it("test_ap_is_a_fully_enabled_pin_with_its_own_consequence_line", async () => {
    // CR-1 (superseding the original v1 draft's disabled-AP scope-cut): AP is a real,
    // selectable pin, confirm-gated exactly like EU/US, naming Vietnam/SEA routing.
    const user = userEvent.setup();
    let putBody: Record<string, unknown> | null = null;
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_UNSET),
      http.put(`${APP}/api/gw/admin/residency-policy`, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ region: "ap", updated_at: "2026-07-12T00:00:00Z" });
      }),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    const apRadio = await screen.findByRole("radio", { name: /^ap$/i });
    expect(apRadio).toBeEnabled();

    await user.click(apRadio);
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/asia-pacific/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/vietnam/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /pin to ap/i }));

    await waitFor(() => expect(putBody).toEqual({ region: "ap" }));
  });

  it("test_non_owner_gets_inline_403_and_reconciles", async () => {
    // M5, R3: a MEMBER sees the SAME picker; PUT 403s; the fieldset shows the
    // existing inline mutError pattern and reconciles the displayed value.
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_EU),
      http.put(`${APP}/api/gw/admin/residency-policy`, () =>
        problem("Forbidden", 403, "ERR_AUTH_FORBIDDEN"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^eu$/i });

    // clearing is the immediate (non-confirm-gated) path — simplest way to exercise
    // "change selection and click Save" without an intervening dialog.
    await user.click(screen.getByRole("radio", { name: /no pin \(unrestricted\)/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/forbidden/i);
    await waitFor(() => expect(screen.getByRole("radio", { name: /^eu$/i })).toBeChecked());
  });

  it("test_defensive_422_reverts_pending_selection", async () => {
    // R2: a 422 (should be unreachable given the FE only ever sends {null,us,eu,ap})
    // never writes the pending selection into state; title shown inline via ConfirmDialog.
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_US),
      http.put(`${APP}/api/gw/admin/residency-policy`, () =>
        problem("Invalid residency region", 422, "ERR_RESIDENCY_REGION_INVALID"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^us$/i });

    await user.click(screen.getByRole("radio", { name: /^eu$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /pin to eu/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/invalid residency region/i);
    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("radio", { name: /^us$/i })).toBeChecked();
  });

  it("test_radios_and_save_disabled_while_confirm_open_no_double_submit", async () => {
    // Safety rule (§5): no second PUT can fire while one is already in flight/pending
    // confirmation — mirrors zdrConfirmOpen's disabled={zdrConfirmOpen} guard.
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      retentionGet(),
      residencyGet(RESIDENCY_UNSET),
      http.put(`${APP}/api/gw/admin/residency-policy`, () => HttpResponse.json(RESIDENCY_EU)),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^eu$/i });

    await user.click(screen.getByRole("radio", { name: /^eu$/i }));
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    await screen.findByRole("dialog");

    expect(screen.getByRole("radio", { name: /^eu$/i })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^us$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("test_residency_fieldset_axe_clean", async () => {
    const user = userEvent.setup();
    server.use(cacheGet(), retentionGet(), residencyGet(RESIDENCY_EU));
    const { container } = render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);
    await screen.findByRole("radio", { name: /^eu$/i });

    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── cross-tenant 404 (representative, one per new tab) ─────────────────────────────
describe("SettingsPage — new tabs cross-tenant 404", () => {
  it("test_cross_tenant_404_no_leak_retention", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/retention-policy`, () =>
        problem("Tenant not found", 404, "ERR_TENANT_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^data & residency$/i);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/tenant not found/i);
    expect(screen.queryByLabelText(/window \(days\)/i)).not.toBeInTheDocument();
  });

  it("test_cross_tenant_404_no_leak_scim", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/scim/tokens`, () =>
        problem("Tenant not found", 404, "ERR_TENANT_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^scim$/i);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/tenant not found/i);
    expect(screen.queryByRole("button", { name: /create token/i })).not.toBeInTheDocument();
  });

  it("test_cross_tenant_404_no_leak_saml", async () => {
    // SAML's 404 is the documented "not configured yet" branch (M7), never an error —
    // it must render the tenant's OWN empty form, not another tenant's data.
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/saml`, () =>
        problem("Not configured", 404, "ERR_SAML_CONFIG_NOT_FOUND"),
      ),
    );
    render(<SettingsPage />, { wrapper: Wrapper });
    await openTab(user, /^saml sso$/i);

    const entityId = await screen.findByLabelText(/idp entity id/i);
    expect(entityId).toHaveValue("");
  });
});

// ── a11y + nav ────────────────────────────────────────────────────────────────────
describe("SettingsPage — axe + nav", () => {
  it("test_settings_axe_clean", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      http.get(`${APP}/api/gw/admin/guardrails`, () => HttpResponse.json(GUARD)),
      http.get(`${APP}/api/gw/admin/oidc`, () => HttpResponse.json(OIDC)),
    );
    const { container } = render(<SettingsPage />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /response cache/i });
    expect(await axeSeriousCritical(container)).toEqual([]);

    await user.click(screen.getByRole("tab", { name: /guardrails/i }));
    await screen.findByRole("switch", { name: /prompt injection/i });
    expect(await axeSeriousCritical(container)).toEqual([]);

    await user.click(screen.getByRole("tab", { name: /^sso$/i }));
    await screen.findByDisplayValue("https://idp.example.com");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_nav_exposes_settings", () => {
    render(
      <AppShell activePath="/app/settings">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /primary/i });
    const link = within(nav).getByRole("link", { name: /settings/i });
    expect(link).toHaveAttribute("href", "/app/settings");
    expect(link).toHaveAttribute("aria-current", "page");
  });

  it("test_scim_saml_retention_axe_clean", async () => {
    const user = userEvent.setup();
    server.use(
      cacheGet(),
      scimGet({ tokens: [SCIM_TOKEN_ACTIVE] }),
      samlGet(),
      retentionGet(),
    );
    const { container } = render(<SettingsPage />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /response cache/i });

    await openTab(user, /^scim$/i);
    await screen.findByText("Okta");
    expect(await axeSeriousCritical(container)).toEqual([]);

    await openTab(user, /^saml sso$/i);
    await screen.findByLabelText(/idp x509 certificate/i);
    expect(await axeSeriousCritical(container)).toEqual([]);

    await openTab(user, /^data & residency$/i);
    await screen.findByLabelText(/window \(days\)/i);
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_new_tabs_keyboard_focus_order", async () => {
    const user = userEvent.setup();
    server.use(cacheGet(), scimGet());
    render(<SettingsPage />, { wrapper: Wrapper });
    await screen.findByRole("switch", { name: /response cache/i });

    await openTab(user, /^scim$/i);
    await screen.findByText(/no scim tokens yet/i);

    const createButton = screen.getByRole("button", { name: /create token/i });
    createButton.focus();
    expect(createButton).toHaveFocus();

    await user.click(createButton);
    const dialog = await screen.findByRole("dialog");
    // Focus moves inside the dialog on open (useFocusTrap) and Escape returns it.
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(createButton).toHaveFocus();
  });
});
