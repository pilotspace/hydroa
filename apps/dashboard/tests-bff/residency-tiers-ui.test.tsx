/**
 * tests-bff/residency-tiers-ui.test.tsx — RED suite for residency-tiers-ui TASK.md §3
 * (FROZEN @ v1, service-tiers-aligned freeze).
 *
 * Covers the parts of the frozen contract that have NO existing companion test file:
 *   - RegionBadge (components/ui/region-badge.tsx, NEW) — the one shared visual (M6).
 *   - TierSelector (components/keys/TierSelector.tsx, NEW) — M9 capacity-preference copy.
 *   - CreateKeyDialog's tier selector + price-delta read (M9/M10/R4/R5) — GET
 *     /admin/service-tiers is the REAL frozen route (service-tiers TASK.md §3 M11):
 *     { default_tier: string, priority_markup_pct: string } (effective, override-or-seed) —
 *     NOT the earlier-drafted { entries: [...] } shape, per residency-tiers-ui TASK.md §3's
 *     own "DECIDED at freeze review... CORRECTED to the real frozen route" note.
 *   - KeysPage -> tier passthrough on create (M9 2nd scenario, R5).
 *
 * Residency fieldset scenarios (M1-M5, R1-R3) live in tests-bff/tenant-settings.test.tsx
 * (RetentionZdrSettings' existing companion). Catalog Region/ineligibility scenarios
 * (M6-M8) live in tests-bff/model-mgmt.test.tsx (ModelsPage's existing companion).
 * Marketing pricing-page copy (M11) lives in tests/pricing-page.test.tsx (existing
 * companion, legacy suite — the page itself has zero auth/fetch surface).
 *
 * RED before Build: `@/components/ui/region-badge` and `@/components/keys/TierSelector`
 * do not exist yet -> MODULE_NOT_FOUND at collect (the established true-red convention,
 * see model-mgmt.test.tsx's own docstring). The CreateKeyDialog/KeysPage tests are
 * BEHAVIORAL-red once those imports resolve (no tier selector rendered yet, onSubmit
 * still single-purpose, POST body carries no `tier`).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { axe } from "@/test-support/axe";
import { server } from "./mocks/server";
import React from "react";

// ── RED: these two files do not exist until Build ────────────────────────────────
import { RegionBadge } from "@/components/ui/region-badge";
import { TierSelector } from "@/components/keys/TierSelector";
import { CreateKeyDialog } from "@/components/keys/CreateKeyDialog";
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

async function axeSeriousCritical(container: HTMLElement) {
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

function serviceTiersGet(body: unknown = { default_tier: "standard", priority_markup_pct: "25" }) {
  return http.get(`${APP}/api/gw/admin/service-tiers`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]),
  );
}

// ── RegionBadge (M6, the one new shared visual) ───────────────────────────────────
describe("RegionBadge", () => {
  it.each([
    ["us", "US"],
    ["eu", "EU"],
    ["ap", "AP"],
    ["global", "GLOBAL"],
  ] as const)("test_region_badge_renders_%s_as_%s", (region, label) => {
    render(<RegionBadge region={region} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("test_region_badge_uses_outline_variant", () => {
    // outline variant: bordered, no bg-primary — a neutral descriptor, not a status
    // (TASK.md §0: "region badges reuse outline — neutral, non-alarming").
    render(<RegionBadge region="eu" />);
    const badge = screen.getByText("EU");
    expect(badge.className).toMatch(/border-border/);
    expect(badge.className).not.toMatch(/bg-primary/);
  });
});

// ── TierSelector (M9 capacity-preference copy + price-delta passthrough) ──────────
describe("TierSelector", () => {
  it("test_tier_selector_defaults_standard_and_shows_capacity_copy", () => {
    render(<TierSelector value="standard" onChange={vi.fn()} priceDelta={null} />);
    expect(screen.getByRole("radio", { name: /standard/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /priority/i })).not.toBeChecked();
    expect(
      screen.getByText(
        "Priority requests get preference under contention and may fall back to Standard when capacity is unavailable — Standard is never starved.",
      ),
    ).toBeInTheDocument();
  });

  it("test_tier_selector_shows_server_sourced_price_delta", () => {
    render(
      <TierSelector value="standard" onChange={vi.fn()} priceDelta="+25% on requests using this key" />,
    );
    expect(screen.getByText("+25% on requests using this key")).toBeInTheDocument();
  });

  it("test_tier_selector_shows_pricing_pending_when_delta_null", () => {
    render(<TierSelector value="standard" onChange={vi.fn()} priceDelta={null} />);
    expect(screen.getByText("Pricing pending")).toBeInTheDocument();
  });

  it("test_tier_selector_calls_onChange_priority", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TierSelector value="standard" onChange={onChange} priceDelta={null} />);
    await user.click(screen.getByRole("radio", { name: /priority/i }));
    expect(onChange).toHaveBeenCalledWith("priority");
  });

  it("test_tier_selector_radios_have_44px_hit_target", () => {
    render(<TierSelector value="standard" onChange={vi.fn()} priceDelta={null} />);
    const standardLabel = screen.getByRole("radio", { name: /standard/i }).closest("label");
    expect(standardLabel?.className).toMatch(/min-h-11/);
  });
});

// ── CreateKeyDialog — tier selector + price-delta (M9/M10/R4) ─────────────────────
describe("CreateKeyDialog — service tier", () => {
  it("test_tier_selector_renders_on_open_with_safe_default", async () => {
    server.use(serviceTiersGet());
    render(<CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />, {
      wrapper: Wrapper,
    });
    expect(await screen.findByRole("radio", { name: /standard/i })).toBeChecked();
    expect(
      screen.getByText(/priority requests get preference under contention/i),
    ).toBeInTheDocument();
  });

  it("test_price_delta_shows_real_server_computed_value", async () => {
    // The REAL frozen shape (service-tiers TASK.md §3 M11): effective priority_markup_pct,
    // NOT a raw multiplier. "25.0000" (Postgres NUMERIC round-trip) formats to "+25%".
    server.use(serviceTiersGet({ default_tier: "standard", priority_markup_pct: "25.0000" }));
    render(<CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />, {
      wrapper: Wrapper,
    });
    expect(await screen.findByText("+25% on requests using this key")).toBeInTheDocument();
  });

  it("test_price_delta_degrades_to_pending_on_404_no_retry", async () => {
    let callCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/service-tiers`, () => {
        callCount += 1;
        return HttpResponse.json({ title: "Not found", status: 404 }, { status: 404 });
      }),
    );
    render(<CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />, {
      wrapper: Wrapper,
    });

    expect(await screen.findByText("Pricing pending")).toBeInTheDocument();
    // the selector itself stays fully rendered + submittable
    expect(screen.getByRole("radio", { name: /priority/i })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: /^create$/i })).not.toBeDisabled();
    // no ErrorState/alert for this expected degrade
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // no retry/poll — the endpoint was hit exactly once
    await new Promise((r) => setTimeout(r, 30));
    expect(callCount).toBe(1);
  });

  it("test_onSubmit_signature_stays_single_arg_name_only", async () => {
    // Regression guard: a pre-existing, OUT-OF-SCOPE suite (tests/keys-dialog-a11y.test.tsx)
    // asserts onSubmit is called with EXACTLY one argument (toHaveBeenCalledWith("ci-key")).
    // Tier must flow through a side-channel (onTierChange), never widen this call signature.
    const user = userEvent.setup();
    server.use(serviceTiersGet());
    const onSubmit = vi.fn(async () => {});
    render(<CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={onSubmit} />, { wrapper: Wrapper });
    await screen.findByText(/priority requests get preference/i);

    await user.type(screen.getByLabelText(/key name/i), "ci-key");
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("ci-key"));
  });

  it("test_selecting_priority_notifies_onTierChange", async () => {
    const user = userEvent.setup();
    server.use(serviceTiersGet());
    const onTierChange = vi.fn();
    render(
      <CreateKeyDialog
        isOpen
        onClose={vi.fn()}
        onSubmit={vi.fn(async () => {})}
        onTierChange={onTierChange}
      />,
      { wrapper: Wrapper },
    );
    await screen.findByText(/priority requests get preference/i);
    await user.click(screen.getByRole("radio", { name: /priority/i }));
    expect(onTierChange).toHaveBeenCalledWith("priority");
  });

  it("test_create_dialog_with_tier_selector_axe_clean", async () => {
    server.use(serviceTiersGet());
    const { container } = render(
      <CreateKeyDialog isOpen onClose={vi.fn()} onSubmit={vi.fn(async () => {})} />,
      { wrapper: Wrapper },
    );
    await screen.findByText(/priority requests get preference/i);
    expect(await axeSeriousCritical(container)).toEqual([]);
  });
});

// ── KeysPage -> tier passthrough on create (M9 2nd scenario, R5) ──────────────────
describe("KeysPage — create key sends tier field", () => {
  it("test_creating_key_with_priority_tier_sends_tier_field", async () => {
    const user = userEvent.setup();
    let postBody: Record<string, unknown> | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json([])),
      serviceTiersGet(),
      http.post(`${APP}/api/gw/admin/keys`, async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ key_id: "kid-1", name: "prod", key: "sk-live.SECRET" }, { status: 201 });
      }),
    );
    render(<KeysPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("button", { name: /create key/i }));
    await screen.findByText(/priority requests get preference/i);
    await user.type(screen.getByLabelText(/key name/i), "prod");
    await user.click(screen.getByRole("radio", { name: /priority/i }));
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(postBody).toEqual({ name: "prod", tier: "priority" }));
    // success path unchanged: dialog closes, plaintext banner shows
    expect(await screen.findByText("sk-live.SECRET")).toBeInTheDocument();
  });

  it("test_creating_key_defaults_to_standard_tier", async () => {
    const user = userEvent.setup();
    let postBody: Record<string, unknown> | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json([])),
      serviceTiersGet(),
      http.post(`${APP}/api/gw/admin/keys`, async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ key_id: "kid-2", name: "dev", key: "sk-live.SECRET2" }, { status: 201 });
      }),
    );
    render(<KeysPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("button", { name: /create key/i }));
    await screen.findByText(/priority requests get preference/i);
    await user.type(screen.getByLabelText(/key name/i), "dev");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(postBody).toEqual({ name: "dev", tier: "standard" }));
  });

  it("test_tier_rejection_surfaces_via_existing_error_branching", async () => {
    // R5: a 422 citing the tier field surfaces via CreateKeyDialog's EXISTING
    // 422->field / else->global branching. No new bespoke error path is added.
    const user = userEvent.setup();
    server.use(
      http.get(`${APP}/api/gw/admin/keys`, () => HttpResponse.json([])),
      serviceTiersGet(),
      http.post(`${APP}/api/gw/admin/keys`, () =>
        HttpResponse.json({ title: "Invalid tier", status: 422, code: "ERR_KEY_TIER_INVALID" }, { status: 422 }),
      ),
    );
    render(<KeysPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("button", { name: /create key/i }));
    await screen.findByText(/priority requests get preference/i);
    await user.type(screen.getByLabelText(/key name/i), "bad-key");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    const dialog = screen.getByRole("dialog", { name: /create api key/i });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/invalid tier/i);
    // dialog stays open, name preserved — the create never succeeded
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/key name/i)).toHaveValue("bad-key");
  });
});
