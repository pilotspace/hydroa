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

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const cacheGet = (body: unknown = CACHE_ON) =>
  http.get(`${APP}/api/gw/admin/cache`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]));

// ── shell + tabs ──────────────────────────────────────────────────────────────
describe("SettingsPage — tab shell", () => {
  it("test_renders_three_tabs_cache_default", async () => {
    server.use(cacheGet());
    render(<SettingsPage />, { wrapper: Wrapper });

    const tablist = await screen.findByRole("tablist");
    expect(within(tablist).getByRole("tab", { name: /cache/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /guardrails/i })).toBeInTheDocument();
    expect(within(tablist).getByRole("tab", { name: /sso/i })).toBeInTheDocument();
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
    await user.click(screen.getByRole("tab", { name: /sso/i }));
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

    await user.click(screen.getByRole("tab", { name: /sso/i }));
    await screen.findByDisplayValue("https://idp.example.com");
    expect(await axeSeriousCritical(container)).toEqual([]);
  });

  it("test_nav_exposes_settings", () => {
    render(
      <AppShell activePath="/settings">
        <div>content</div>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: /primary/i });
    const link = within(nav).getByRole("link", { name: /settings/i });
    expect(link).toHaveAttribute("href", "/settings");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
