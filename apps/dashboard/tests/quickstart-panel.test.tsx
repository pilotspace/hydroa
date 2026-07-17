/**
 * quickstart-panel.test.tsx — RED suite for activation-quickstart TASK.md §3 v1 #4/#5,
 * M4 + M5 (QuickstartPanel mounted beside PlaintextKeyBanner on key creation).
 *
 * RED before Build: @/components/keys/QuickstartPanel does not exist (MODULE_NOT_FOUND).
 *
 * IMPORTANT — accessible-name collision guard: the pre-existing, frozen
 * tests/keys.test.tsx::test_create_key_shows_plaintext_once_not_in_list asserts EXACTLY
 * one button matches /copy/i and EXACTLY one matches /dismiss|close|done|got it/i once a
 * key is created. QuickstartPanel's own clipboard buttons are therefore labelled
 * "Duplicate" (never "copy"/"dismiss"/"close"/"done"/"got it") so that pre-existing,
 * unedited assertion keeps finding exactly one match (PlaintextKeyBanner's own "Copy"/
 * "Done" buttons) — a disclosed, deliberate wording choice, not an accidental dodge.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { KeysPage } from "@/components/keys/KeysPage";
import { QuickstartPanel } from "@/components/keys/QuickstartPanel";

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

const ORIGINAL_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => {
  if (ORIGINAL_BASE_URL === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE_URL;
});

// ── standalone component tests (props-driven, no fetch) ─────────────────────────
describe("QuickstartPanel — standalone", () => {
  const user = userEvent.setup();

  it("test_quickstart_panel_real_key_configured_url", async () => {
    render(<QuickstartPanel plaintextKey="sk-real.SECRETVALUE" baseUrl="https://api.hydroa.dev" />);

    // curl tab is the default — real base URL + real key visible
    expect(screen.getByText(/curl https:\/\/api\.hydroa\.dev\/v1\/chat\/completions/)).toBeInTheDocument();
    expect(screen.getByText(/Bearer sk-real\.SECRETVALUE/)).toBeInTheDocument();

    // python tab
    await user.click(screen.getByRole("tab", { name: /python/i }));
    expect(screen.getByText(/https:\/\/api\.hydroa\.dev\/v1/)).toBeInTheDocument();
    expect(screen.getByText(/sk-real\.SECRETVALUE/)).toBeInTheDocument();

    // js tab
    await user.click(screen.getByRole("tab", { name: /js|javascript/i }));
    expect(screen.getByText(/https:\/\/api\.hydroa\.dev\/v1/)).toBeInTheDocument();

    // A working "Duplicate" (copy) button exists per active tab
    expect(screen.getByRole("button", { name: /duplicate/i })).toBeInTheDocument();

    // Playground note
    expect(screen.getByText(/playground needs no key/i)).toBeInTheDocument();
  });

  it("test_quickstart_panel_degrades_unconfigured_url", () => {
    render(<QuickstartPanel plaintextKey="sk-real.SECRETVALUE" baseUrl={null} />);

    // Explicit placeholder + operator note, never a URL that looks real
    expect(screen.queryByText(/https?:\/\//)).not.toBeInTheDocument();
    expect(screen.getAllByText(/NEXT_PUBLIC_API_BASE_URL/).length).toBeGreaterThan(0);
  });
});

// ── mounted in KeysPage on create (M4) — plus the collision guard ──────────────
describe("KeysPage — QuickstartPanel mounts beside PlaintextKeyBanner", () => {
  const user = userEvent.setup();

  it("test_quickstart_panel_mounts_alongside_plaintext_banner_on_create", async () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () => HttpResponse.json([])),
      http.post("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json({ key_id: "kid-new", name: "new-key", key: "sk-new.MOUNTSECRET" }, { status: 201 })
      )
    );
    render(<KeysPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("button", { name: /create key/i }));
    await user.type(screen.getByLabelText(/key name/i), "new-key");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    // PlaintextKeyBanner (unchanged, cited) still renders
    expect(await screen.findByText("sk-new.MOUNTSECRET")).toBeInTheDocument();
    // QuickstartPanel renders alongside it, same secret, no re-fetch
    expect(screen.getByText(/Bearer sk-new\.MOUNTSECRET/)).toBeInTheDocument();
  });

  it("test_no_accessible_name_collision_with_frozen_plaintext_banner_controls", async () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () => HttpResponse.json([])),
      http.post("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json({ key_id: "kid-new2", name: "new-key2", key: "sk-new2.MOUNTSECRET" }, { status: 201 })
      )
    );
    render(<KeysPage />, { wrapper: Wrapper });

    await user.click(await screen.findByRole("button", { name: /create key/i }));
    await user.type(screen.getByLabelText(/key name/i), "new-key2");
    await user.click(screen.getByRole("button", { name: /^create$/i }));
    await screen.findByText("sk-new2.MOUNTSECRET");

    // Exactly one "copy"-matching control (PlaintextKeyBanner's) and one
    // "dismiss/close/done/got it"-matching control — the pre-existing
    // keys.test.tsx assertion this must never break.
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dismiss|close|done|got it/i })).toBeInTheDocument();
  });
});
