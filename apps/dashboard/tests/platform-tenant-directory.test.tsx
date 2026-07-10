/**
 * tests/platform-tenant-directory.test.tsx — RED suite for Screen 1 of the
 * admin-console-ui task: the superadmin-only cross-tenant directory
 * (/app/platform/tenants — TASK.md §3 CONTRACT, SCREEN 1).
 *
 * GET /admin/platform/tenants?q=&limit=&offset= -> TenantDirectoryListResponse
 * { tenants: [{id, name, kind, created_at}], total } (platform_tenants_router.py,
 * FROZEN @ v1, read directly this session — cited verbatim, not redefined here).
 *
 * RED before Build: `@/components/platform/PlatformTenantDirectory` does not
 * exist yet -> MODULE_NOT_FOUND, the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformTenantDirectory } from "@/components/platform/PlatformTenantDirectory";

const APP = "http://localhost:3000";
const LONG_NAME = "Northwind Traders International Holdings Group Ltd.";

const FIVE_TENANTS = {
  tenants: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      name: "Acme Robotics",
      kind: "standard",
      created_at: "2026-01-15T00:00:00Z",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      name: "Globex Corp",
      kind: "standard",
      created_at: "2026-02-20T00:00:00Z",
    },
    {
      id: "33333333-3333-3333-3333-333333333333",
      name: "Initech",
      kind: "standard",
      created_at: "2026-03-01T00:00:00Z",
    },
    {
      id: "44444444-4444-4444-4444-444444444444",
      name: LONG_NAME,
      kind: "standard",
      created_at: "2026-03-05T00:00:00Z",
    },
    {
      id: "00000000-0000-0000-0000-000000000099",
      name: "Platform Tenant",
      kind: "platform",
      created_at: "2026-01-01T00:00:00Z",
    },
  ],
  total: 5,
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderDirectory() {
  const client = makeQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <PlatformTenantDirectory />
    </QueryClientProvider>,
  );
}

function section() {
  return screen.getByRole("region", { name: /tenants/i });
}

describe("PlatformTenantDirectory", () => {
  it("test_directory_search_filters_and_row_links_to_detail", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, ({ request }) => {
        const url = new URL(request.url);
        const q = url.searchParams.get("q");
        if (q && q.toLowerCase() === "acme") {
          return HttpResponse.json({ tenants: [FIVE_TENANTS.tenants[0]], total: 1 });
        }
        return HttpResponse.json(FIVE_TENANTS);
      }),
    );

    renderDirectory();

    await waitFor(() => {
      expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
    });
    expect(within(section()).getByText("Globex Corp")).toBeInTheDocument();

    const searchBox = within(section()).getByRole("textbox", { name: /search tenants/i });
    fireEvent.change(searchBox, { target: { value: "acme" } });

    // Both conditions must hold in the SAME poll: "Globex Corp" briefly disappears
    // during the interim loading-spinner render too (the whole table unmounts while
    // the new key is in flight), so checking absence alone can resolve before the
    // filtered "acme" data has actually arrived. Requiring "Acme Robotics" present
    // in the same predicate anchors this on the real end state.
    await waitFor(
      () => {
        expect(within(section()).queryByText("Globex Corp")).not.toBeInTheDocument();
        expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
      },
      { timeout: 2000 },
    );

    const link = within(section()).getByRole("link", { name: "Acme Robotics" });
    expect(link).toHaveAttribute(
      "href",
      "/app/platform/tenants/11111111-1111-1111-1111-111111111111",
    );
  });

  it("test_directory_previous_disabled_on_first_page", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(FIVE_TENANTS)),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
    });
    const prevButton = within(section()).getByRole("button", { name: /previous/i });
    expect(prevButton).toBeDisabled();
  });

  it("test_directory_empty_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json({ tenants: [], total: 0 }),
      ),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText(/no tenants/i)).toBeInTheDocument();
    });
    expect(within(section()).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_directory_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () =>
        HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 }),
      ),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText(/internal server error/i)).toBeInTheDocument();
    });
    // no partial/malformed table renders alongside the error (R1)
    expect(within(section()).queryByRole("table")).not.toBeInTheDocument();
  });

  it("test_directory_long_name_truncates_with_title", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(FIVE_TENANTS)),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText(LONG_NAME)).toBeInTheDocument();
    });
    const link = within(section()).getByRole("link", { name: LONG_NAME });
    expect(link).toHaveAttribute("title", LONG_NAME);
    expect(link.className).toContain("truncate");
  });

  it("test_directory_kind_badge_variants", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(FIVE_TENANTS)),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
    });
    // FIVE_TENANTS has 4 standard-kind rows (Acme/Globex/Initech/Northwind) — scope
    // to Acme's own row so this reads a single, known-standard badge rather than an
    // ambiguous getByText across all 4 (a query-precision fix, not a weaker assertion:
    // the comparison below is unchanged — a real standard badge vs. the one platform badge).
    const acmeRow = within(section()).getByRole("row", { name: /Acme Robotics/i });
    const standardBadge = within(acmeRow).getByText("Standard");
    const platformBadge = within(section()).getByText("Platform");
    expect(standardBadge.className).not.toEqual(platformBadge.className);
  });

  // console-flat-visual-pass, 2026-07-06 (M1) — the Card wrapping DataTable goes
  // from variant="soft" to variant="flat" (sharp radius, no border, no shadow).
  it("test_directory_card_renders_flat_variant", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(FIVE_TENANTS)),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
    });
    const table = within(section()).getByRole("table");
    const card = table.closest("[data-variant]")!;
    expect(card).toHaveAttribute("data-variant", "flat");
    expect(card.className).toContain("rounded-[var(--radius-flat-card)]");
    expect(card.className).toContain("shadow-none");
    expect(card.className).not.toContain("border-border");
    expect(card.className).not.toContain("shadow-md");
    expect(card.className).not.toContain("rounded-2xl");
  });

  // console-flat-visual-pass (M5) — the search Input and the 2 Kind Badge chips get
  // a page-local flat-control/flat-tag radius override; the raw pagination <button>s
  // are hand-rolled HTML (not the Button component) and are explicitly OUT of scope.
  it("test_directory_search_input_and_kind_badges_get_flat_radius_class", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(FIVE_TENANTS)),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
    });

    const searchBox = within(section()).getByRole("textbox", { name: /search tenants/i });
    expect(searchBox.className).toContain("rounded-[var(--radius-flat-control)]");

    const acmeRow = within(section()).getByRole("row", { name: /Acme Robotics/i });
    const standardBadge = within(acmeRow).getByText("Standard");
    expect(standardBadge.className).toContain("rounded-[var(--radius-flat-tag)]");
    const platformBadge = within(section()).getByText("Platform");
    expect(platformBadge.className).toContain("rounded-[var(--radius-flat-tag)]");

    // Raw pagination <button>s are hand-rolled HTML, explicitly OUT of M5's scope —
    // they keep their existing rounded-md, never the flat-control override.
    const prevButton = within(section()).getByRole("button", { name: /previous/i });
    expect(prevButton.className).toContain("rounded-md");
    expect(prevButton.className).not.toContain("rounded-[var(--radius-flat-control)]");
  });

  // console-flat-visual-pass (R2, reject scenario) — visual-only pass: no new
  // Tier-3 kind/plan filter or bulk-action control anywhere in this screen.
  it("test_directory_no_new_filter_or_bulk_action_ia_introduced", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/platform/tenants`, () => HttpResponse.json(FIVE_TENANTS)),
    );
    renderDirectory();
    await waitFor(() => {
      expect(within(section()).getByText("Acme Robotics")).toBeInTheDocument();
    });
    expect(within(section()).queryByRole("combobox")).not.toBeInTheDocument();
    expect(within(section()).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      within(section()).queryByRole("button", { name: /bulk|kind filter|plan filter/i }),
    ).not.toBeInTheDocument();
  });
});
