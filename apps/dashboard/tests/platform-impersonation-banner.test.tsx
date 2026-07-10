/**
 * tests/platform-impersonation-banner.test.tsx — RED suite for the impersonation-ui
 * task's new ImpersonationBanner (TASK.md §3 CONTRACT Part C, M8): the persistent,
 * shell-level "you are impersonating" signal — target identity, actor identity
 * (when resolved, omitted rather than fabricated when null), a live countdown
 * derived from expires_at (available on every active response, fresh load or not,
 * per the M1 cookie envelope — never dependent on same-tab mint memory), and an
 * End control behind a role="dialog" + useFocusTrap confirm step mirroring
 * PlatformKeysTab's own revoke-confirm exactly (see tests/platform-keys.test.tsx's
 * test_revoke_key_requires_focus_trapped_confirm).
 *
 * RED before Build: `@/components/platform/ImpersonationBanner` does not exist yet
 * -> MODULE_NOT_FOUND (the whole file fails to load, so every test below reports
 * failed/errored — the declared RED reason for this file).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { ImpersonationBanner } from "@/components/platform/ImpersonationBanner";

const APP = "http://localhost:3000";
const SESSION_ID = "33333333-3333-3333-3333-333333333333";

const TARGET = { user_id: "u-1", tenant_id: "t-1", email: "target@acme.io", role: "member" };
const ACTOR = { email: "root@platform.internal", role: "superadmin" };

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderBanner(client: QueryClient = makeQueryClient()) {
  return render(
    <QueryClientProvider client={client}>
      <ImpersonationBanner />
    </QueryClientProvider>,
  );
}

function mockStatusInactive() {
  server.use(
    http.get(`${APP}/api/platform/impersonation`, () => HttpResponse.json({ active: false })),
  );
}

function mockStatusActive(expiresAt: number) {
  server.use(
    http.get(`${APP}/api/platform/impersonation`, () =>
      HttpResponse.json({
        active: true,
        session_id: SESSION_ID,
        expires_at: expiresAt,
        target: TARGET,
        actor: ACTOR,
      }),
    ),
  );
}

describe("ImpersonationBanner", () => {
  it("test_banner_hidden_when_inactive", async () => {
    mockStatusInactive();
    renderBanner();

    await waitFor(() => {
      expect(screen.queryByTestId("impersonation-banner")).not.toBeInTheDocument();
    });
  });

  it("test_banner_shows_target_and_actor_when_active", async () => {
    mockStatusActive(Date.now() + 15 * 60 * 1000);
    renderBanner();

    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });
    const banner = screen.getByTestId("impersonation-banner");
    expect(banner).toHaveTextContent(/target@acme\.io/i);
    expect(banner).toHaveTextContent(/member/i);
    expect(banner).toHaveTextContent(/root@platform\.internal/i);
  });

  it("test_banner_countdown_survives_fresh_load_via_cookie_envelope", async () => {
    // A FRESH QueryClient with no seeded cache simulates a hard refresh — there is
    // no same-tab mint memory. expires_at still arrives on the status response
    // (sourced server-side from the M1 cookie envelope, not from client memory),
    // so the countdown must render identically to the same-tab-mint case.
    const freshClient = makeQueryClient();
    mockStatusActive(Date.now() + 5 * 60 * 1000);
    renderBanner(freshClient);

    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });
    const banner = screen.getByTestId("impersonation-banner");
    // Target/actor resolve correctly even with zero client-side mint memory.
    expect(banner).toHaveTextContent(/target@acme\.io/i);
    expect(banner).toHaveTextContent(/root@platform\.internal/i);
    // A live countdown derived from expires_at — tolerant of Build's exact copy/
    // format (e.g. "4:59" vs "04:59"), just requires a digit-clock-shaped string
    // somewhere in the banner (behavior, not pixels).
    expect(banner.textContent ?? "").toMatch(/\b\d{1,2}:\d{2}\b/);
  });

  it("test_banner_end_requires_confirm_before_calling_end", async () => {
    let endCalled = false;
    mockStatusActive(Date.now() + 15 * 60 * 1000);
    server.use(
      http.delete(`${APP}/api/platform/impersonation/sessions/${SESSION_ID}`, () => {
        endCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderBanner();

    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /end impersonation/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(endCalled).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /end impersonation/i }));
    const dialog2 = await screen.findByRole("dialog");
    fireEvent.click(within(dialog2).getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(endCalled).toBe(true);
    });
  });

  it("test_banner_end_success_clears_query_cache", async () => {
    const client = makeQueryClient();
    client.setQueryData(["some-unrelated-query"], { foo: "bar" });

    let endCalled = false;
    mockStatusActive(Date.now() + 15 * 60 * 1000);
    server.use(
      http.delete(`${APP}/api/platform/impersonation/sessions/${SESSION_ID}`, () => {
        endCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderBanner(client);

    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /end impersonation/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /confirm/i }));

    await waitFor(() => {
      expect(endCalled).toBe(true);
    });
    // M10: End's onSuccess calls queryClient.clear() — the FULL cache, including
    // identity-independent, unrelated queries (the deliberately blunt instrument
    // against cross-boundary cache leakage — §1 ⚠).
    expect(client.getQueryData(["some-unrelated-query"])).toBeUndefined();
  });

  // console-flat-visual-pass, 2026-07-06 (M3) — same quieted-divider recipe as
  // PlatformSafetyBanner (Issues/Risks #2): border-warning/40 -> border-border;
  // bg-warning/10, text-warning-foreground, and the icon's text-warning stay
  // unchanged (literal "divider" reading — see TASK.md §5 Known-problem fixes).
  it("test_banner_divider_quieted_bg_text_icon_unchanged", async () => {
    mockStatusActive(Date.now() + 15 * 60 * 1000);
    renderBanner();

    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });
    const banner = screen.getByTestId("impersonation-banner");
    expect(banner.className).toContain("border-border");
    expect(banner.className).not.toContain("border-warning/40");
    expect(banner.className).toContain("bg-warning/10");
    expect(banner.className).toContain("text-warning-foreground");
    const icon = banner.querySelector("svg");
    expect(icon).not.toBeNull();
    expect(icon!.getAttribute("class") ?? "").toContain("text-warning");
  });

  // console-flat-visual-pass (M5, negative) — the banner is shell-level chrome
  // around the 6 screens, not one of the 6 screens itself; its "End impersonation"
  // trigger keeps the shared Button default radius, untouched by M5.
  it("test_end_impersonation_button_not_touched_by_m5_page_local_radius", async () => {
    mockStatusActive(Date.now() + 15 * 60 * 1000);
    renderBanner();
    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });
    const button = screen.getByRole("button", { name: /end impersonation/i });
    expect(button.className).toContain("rounded-md");
    expect(button.className).not.toContain("rounded-[var(--radius-flat-control)]");
  });

  // console-flat-visual-pass (M6, verify-only) — this banner's own end-confirm
  // dialog is structurally identical to Keys/Members/Plan's elevated-modal
  // pattern (same underlying principle, per TASK.md's M6 4th case) and stays
  // unchanged too.
  it("test_end_impersonation_confirm_dialog_stays_elevated_unchanged", async () => {
    mockStatusActive(Date.now() + 15 * 60 * 1000);
    renderBanner();
    await waitFor(() => {
      expect(screen.getByTestId("impersonation-banner")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /end impersonation/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("rounded-lg");
    expect(dialog.className).toContain("border-border");
    expect(dialog.className).toContain("shadow-lg");
    expect(dialog.className).not.toContain("rounded-[var(--radius-flat-card)]");
  });
});
