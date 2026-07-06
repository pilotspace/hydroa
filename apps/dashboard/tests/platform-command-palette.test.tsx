/**
 * tests/platform-command-palette.test.tsx — RED suite for command-palette
 * TASK.md's own PlatformCommandPalette component (§3 CONTRACT).
 *
 * Scope: the component's OWN behavior, mounted DIRECTLY (no DashboardShell,
 * no useCurrentUser mocking) — per the frozen contract, "this component
 * performs NO internal role re-check ... its very presence in the tree IS the
 * frontend gate." The MOUNT-GATE tests themselves (M1/M2/R2 — does
 * DashboardShell actually mount this component if and only if role ===
 * "superadmin") live in tests/design-system/app-shell-sidebar.test.tsx
 * (extended by this same task), which already has the DashboardShell +
 * useCurrentUser mocking infrastructure this scenario needs.
 *
 * RED before Build: `@/components/platform/PlatformCommandPalette` does not
 * exist yet -> MODULE_NOT_FOUND, the established true-red convention (mirrors
 * platform-tenant-directory.test.tsx / platform-tenant-overview-strip.test.tsx).
 *
 * GET /admin/platform/tenants?q=&limit=&offset= -> TenantDirectoryListResponse
 * (platform_tenants_router.py, FROZEN @ v1, cited verbatim by TASK.md §3 — the
 * SAME backend endpoint PlatformTenantDirectory.tsx already calls; this task
 * makes zero backend change).
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import fs from "node:fs";
import path from "node:path";
import React from "react";

import { PlatformCommandPalette } from "@/components/platform/PlatformCommandPalette";
import { PlatformTenantDirectory } from "@/components/platform/PlatformTenantDirectory";

const APP = "http://localhost:3000";

const THREE_TENANTS = {
  tenants: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      name: "Acme Robotics",
      kind: "standard",
      created_at: "2026-01-15T00:00:00Z",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      name: "Acme Logistics",
      kind: "standard",
      created_at: "2026-02-20T00:00:00Z",
    },
    {
      id: "33333333-3333-3333-3333-333333333333",
      name: "Acme Foods",
      kind: "standard",
      created_at: "2026-03-01T00:00:00Z",
    },
  ],
  total: 3,
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderPalette(client: QueryClient = makeQueryClient()) {
  return render(
    <QueryClientProvider client={client}>
      <PlatformCommandPalette />
    </QueryClientProvider>,
  );
}

async function openPalette(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /search tenants.*command k/i }));
}

function searchInput() {
  return screen.getByRole("textbox", { name: /search tenants by name/i });
}

beforeEach(() => {
  const router = getRouterMock();
  router.push.mockClear();
  router.replace.mockClear();
});

// ── M3, M4: global shortcut + always-visible trigger ────────────────────────
describe("PlatformCommandPalette — opening (M3, M4)", () => {
  it("test_global_shortcut_opens_and_toggles_the_palette", async () => {
    renderPalette();
    expect(screen.queryByRole("dialog")).toBeNull();

    const event = new KeyboardEvent("keydown", {
      key: "k",
      metaKey: true,
      bubbles: true,
      cancelable: true,
    });
    const preventDefaultSpy = vi.spyOn(event, "preventDefault");
    document.dispatchEvent(event);

    expect(preventDefaultSpy).toHaveBeenCalled();
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    // pressing the identical shortcut again while open closes it (toggle)
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // Ctrl+K works identically to Cmd+K, case-insensitively ("K" vs "k")
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "K", ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("test_trigger_control_opens_the_palette_without_a_keyboard", async () => {
    const user = userEvent.setup();
    renderPalette();
    const trigger = screen.getByRole("button", { name: /search tenants.*command k/i });
    // always-visible at every breakpoint — never gated behind a responsive
    // "hidden"/"lg:hidden" class (M4's "not desktop-only" requirement)
    expect(trigger.className).not.toMatch(/(^|\s)hidden(\s|$)/);
    expect(trigger.className).not.toMatch(/lg:hidden/);

    await user.click(trigger);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

// ── M5, M6: focus management ─────────────────────────────────────────────────
describe("PlatformCommandPalette — focus management (M5, M6)", () => {
  it("test_focus_is_trapped_and_returns_to_the_triggering_element_on_close", async () => {
    // M5 relies on Radix's Dialog focus behavior VERBATIM (zero new focus-
    // management code, per the frozen §3 CONTRACT). Radix's own documented
    // default returns focus specifically to the registered DialogTrigger
    // element on close (its `onCloseAutoFocus` default calls
    // `context.triggerRef.current?.focus()`), not generically to "whatever
    // last had focus" regardless of how the dialog was opened. Opening via a
    // direct click on the trigger makes "the element with focus before
    // opening" and "the triggering element" the SAME element, so this test
    // exercises that real, zero-ambiguity default rather than asserting a
    // stricter behavior Radix does not actually implement without new code.
    const user = userEvent.setup();
    renderPalette();
    const trigger = screen.getByRole("button", { name: /search tenants.*command k/i });

    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    await user.click(trigger);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);

    // focus stays trapped inside the dialog through repeated Tabbing
    for (let i = 0; i < 5; i += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("test_search_input_is_focused_automatically_on_open", async () => {
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    expect(document.activeElement).toBe(searchInput());
  });
});

// ── M7, M8, R4: search, debounce, distinct cache key ─────────────────────────
describe("PlatformCommandPalette — search debounce + cache identity (M7, M8, R4)", () => {
  it("test_typed_query_is_debounced_before_it_fires", async () => {
    let requestCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => {
        requestCount += 1;
        return HttpResponse.json(THREE_TENANTS);
      }),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);

    fireEvent.change(searchInput(), { target: { value: "acme" } });
    expect(requestCount).toBe(0); // not yet — still inside the 300ms debounce window

    await waitFor(() => expect(requestCount).toBe(1), { timeout: 2000 });

    // whitespace-only never enables the fetch at all
    fireEvent.change(searchInput(), { target: { value: "   " } });
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(requestCount).toBe(1); // unchanged
  });

  it("test_results_query_uses_its_own_distinct_cache_key_against_the_shared_endpoint", async () => {
    let capturedUrl: URL | null = null;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(THREE_TENANTS);
      }),
    );
    const client = makeQueryClient();
    const user = userEvent.setup();
    renderPalette(client);
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });

    await waitFor(() => expect(capturedUrl).not.toBeNull());
    expect(capturedUrl!.searchParams.get("q")).toBe("acme");
    expect(capturedUrl!.searchParams.get("limit")).toBe("8");
    expect(capturedUrl!.searchParams.get("offset")).toBe("0");

    await waitFor(() => {
      const keys = client.getQueryCache().getAll().map((q) => q.queryKey);
      expect(keys).toContainEqual(["platform-tenant-search", "acme"]);
    });
  });

  it("test_reject_no_shared_cache_key_with_the_tenant_directory", async () => {
    let directoryRequests = 0;
    let paletteRequests = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get("q") === "acme") {
          if (url.searchParams.get("limit") === "20") directoryRequests += 1;
          if (url.searchParams.get("limit") === "8") paletteRequests += 1;
        }
        return HttpResponse.json(THREE_TENANTS);
      }),
    );
    const client = makeQueryClient();
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <PlatformTenantDirectory />
        <PlatformCommandPalette />
      </QueryClientProvider>,
    );

    // PlatformTenantDirectory's own search Input only renders once its
    // initial (empty-query) fetch resolves — wait for that first, mirroring
    // that component's own test file convention.
    const directorySearch = await screen.findByRole("textbox", { name: /^search tenants$/i });
    fireEvent.change(directorySearch, { target: { value: "acme" } });

    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });

    await waitFor(() => {
      expect(directoryRequests).toBe(1);
      expect(paletteRequests).toBe(1);
    });

    await waitFor(() => {
      const keys = client.getQueryCache().getAll().map((q) => q.queryKey);
      expect(keys).toContainEqual(["platform-tenants", "acme", 0]);
      expect(keys).toContainEqual(["platform-tenant-search", "acme"]);
    });
  });
});

// ── M9, M10, M11: listbox, navigation, close-resets-query ────────────────────
describe("PlatformCommandPalette — results listbox + navigation (M9, M10, M11)", () => {
  it("test_results_render_as_aria_listbox_with_clamped_arrow_navigation", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(THREE_TENANTS)),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });

    const listbox = await screen.findByRole("listbox", { name: /tenant search results/i });
    const options = within(listbox).getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(options[1]).toHaveAttribute("aria-selected", "false");
    expect(options[2]).toHaveAttribute("aria-selected", "false");

    await user.keyboard("{ArrowDown}");
    expect(options[0]).toHaveAttribute("aria-selected", "false");
    expect(options[1]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}");
    await user.keyboard("{ArrowDown}"); // attempt to go past the last item — clamps
    expect(options[2]).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowUp}");
    await user.keyboard("{ArrowUp}");
    await user.keyboard("{ArrowUp}"); // attempt to go before the first item — clamps
    expect(options[0]).toHaveAttribute("aria-selected", "true");
  });

  it("test_enter_and_click_both_navigate_to_the_selected_tenant", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(THREE_TENANTS)),
    );
    const router = getRouterMock();
    const user = userEvent.setup();

    // Enter path — second result highlighted, then Enter
    const firstRender = renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });
    await screen.findByRole("listbox", { name: /tenant search results/i });
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");
    expect(router.push).toHaveBeenCalledWith(
      "/app/platform/tenants/22222222-2222-2222-2222-222222222222",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    firstRender.unmount();

    router.push.mockClear();

    // Click path — a fresh render, click a different result directly
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });
    const listbox2 = await screen.findByRole("listbox", { name: /tenant search results/i });
    const options2 = within(listbox2).getAllByRole("option");
    await user.click(options2[2]);
    expect(router.push).toHaveBeenCalledWith(
      "/app/platform/tenants/33333333-3333-3333-3333-333333333333",
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("test_closing_by_any_path_resets_the_query_for_next_time", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(THREE_TENANTS)),
    );
    const router = getRouterMock();
    const user = userEvent.setup();
    renderPalette();

    // Escape close, no selection -> no navigation
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });
    await screen.findByRole("listbox", { name: /tenant search results/i });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(router.push).not.toHaveBeenCalled();

    // reopen -> prompt state, not the previous search
    await openPalette(user);
    const reopenedInput = searchInput();
    expect(reopenedInput).toHaveValue("");
    expect(screen.getByText(/type to search tenants by name/i)).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).toBeNull();

    // shortcut-toggle close, no selection -> no navigation
    fireEvent.change(reopenedInput, { target: { value: "acme" } });
    await screen.findByRole("listbox", { name: /tenant search results/i });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(router.push).not.toHaveBeenCalled();
  });
});

// ── M12-M15, R6: render states ────────────────────────────────────────────────
describe("PlatformCommandPalette — render states (M12, M13, M14, M15, R6)", () => {
  it("test_empty_query_shows_a_prompt_not_a_fetch", async () => {
    let requestCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => {
        requestCount += 1;
        return HttpResponse.json(THREE_TENANTS);
      }),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);

    expect(screen.getByText(/type to search tenants by name/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();

    await new Promise((resolve) => setTimeout(resolve, 350)); // give a would-be bug time to fire
    expect(requestCount).toBe(0);
  });

  it("test_no_matches_shows_the_shared_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json({ tenants: [], total: 0 }),
      ),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "zzz" } });

    await waitFor(() => expect(screen.getByText(/no tenants found/i)).toBeInTheDocument());
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("test_loading_and_error_states_reuse_the_shared_primitives", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
        return HttpResponse.json(THREE_TENANTS);
      }),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());

    // separately, a failing query
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 }),
      ),
    );
    fireEvent.change(searchInput(), { target: { value: "fail" } });
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText(/internal server error/i)).toBeInTheDocument();
  });

  it("test_more_matches_than_shown_surfaces_a_refine_hint", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json({ tenants: THREE_TENANTS.tenants, total: 25 }),
      ),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });

    await screen.findByRole("listbox", { name: /tenant search results/i });
    expect(screen.getByText(/showing 3 of 25/i)).toBeInTheDocument();
  });

  it("test_reject_no_pagination_inside_the_palette", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json({ tenants: THREE_TENANTS.tenants, total: 25 }),
      ),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });
    const dialog = await screen.findByRole("dialog");

    await waitFor(() => expect(within(dialog).getByRole("listbox")).toBeInTheDocument());
    expect(within(dialog).queryByRole("button", { name: /previous|next|page/i })).toBeNull();
    expect(within(dialog).queryByRole("navigation")).toBeNull();
  });
});

// ── R1, R3, R5: reject scenarios ──────────────────────────────────────────────
describe("PlatformCommandPalette — reject scenarios (R1, R3, R5)", () => {
  it("test_reject_no_command_execution_beyond_search_and_navigate", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(THREE_TENANTS)),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByRole("listbox")).toBeInTheDocument());

    expect(
      within(dialog).queryByRole("button", { name: /create|revoke|impersonate|delete|edit/i }),
    ).toBeNull();
    expect(within(dialog).queryByRole("menuitem")).toBeNull();
  });

  it("test_reject_backend_gate_is_never_the_only_safeguard", async () => {
    // Simulates the frontend mount gate being bypassed (e.g. a stale/misread
    // role slipped past DashboardShell) by mounting PlatformCommandPalette
    // directly. The already-shipped, unmodified require_superadmin dependency
    // (apps/gateway/src/gateway/tenants/domain/authz.py — NOT touched by this
    // task) independently rejects a non-superadmin caller with 403; this
    // proves the component degrades to the shared ErrorState — never
    // fabricating or otherwise exposing tenant data — so data safety never
    // depends on the frontend mount gate alone.
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json(
          { title: "Forbidden", status: 403, code: "ERR_AUTH_FORBIDDEN" },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderPalette();
    await openPalette(user);
    fireEvent.change(searchInput(), { target: { value: "acme" } });

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByText(/acme robotics/i)).not.toBeInTheDocument();
  });

  it("test_reject_no_new_general_purpose_combobox_primitive", () => {
    const uiDir = path.resolve(__dirname, "../components/ui");
    const files = fs.readdirSync(uiDir);
    const suspicious = files.filter((f) => /command|combobox/i.test(f));
    expect(suspicious).toHaveLength(0);

    // the listbox lives inside the new platform component file, not ui/
    const paletteSource = fs.readFileSync(
      path.resolve(__dirname, "../components/platform/PlatformCommandPalette.tsx"),
      "utf-8",
    );
    expect(paletteSource).toMatch(/role="listbox"/);
  });
});
