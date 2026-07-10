/**
 * tests/platform-activity-tab.test.tsx — RED suite for tenant-activity-tab's own
 * PlatformActivityTab component (TASK.md §3 CONTRACT).
 *
 * Structural precedent: PlatformMembersTab.tsx ({tenantId}-keyed own useQuery, local
 * getErrorTitle, Loading/ErrorState early-return) with PlatformTenantDirectory.tsx's own
 * server-driven Previous/Next pagination chrome grafted on top (§1 Framings weighed).
 *
 * GET /admin/platform/tenants/{tenant_id}/audit -> AuditListResponse
 * { items: [{id, actor_email, action, target_type, target_id, result, metadata, created_at}],
 *   total } (platform_audit_router.py, FROZEN @ v1, cited verbatim by TASK.md §3 — a NEW
 * superadmin-scoped route this same task's own backend half just built).
 *
 * Covers this task's own FRONTEND scenarios (§2): M8-M13, R1-R5, R9. M7 (the 6th-tab wiring
 * into PlatformTenantDetail) lives in tests/platform-tenant-detail.test.tsx (extended) instead,
 * matching how the Members/Config/Budget/Keys tabs' own scenarios live in their own dedicated
 * files while the shell-level wiring test lives in platform-tenant-detail.test.tsx.
 *
 * RED before Build: `@/components/platform/PlatformActivityTab` does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention (mirrors platform-members.test.tsx /
 * platform-command-palette.test.tsx).
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse, delay } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { PlatformActivityTab } from "@/components/platform/PlatformActivityTab";

const APP = "http://localhost:3000";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";
const OTHER_TENANT_ID = "22222222-2222-2222-2222-222222222222";
const AUDIT_PATH = `${APP}/api/gw/admin/platform/tenants/${TENANT_ID}/audit`;

function threeRows() {
  return {
    items: [
      {
        id: "a-1",
        actor_email: "alice@acme.io",
        action: "key.create",
        target_type: "api_key",
        target_id: "k-1",
        result: "success",
        metadata: {},
        created_at: "2026-07-06T12:03:00Z",
      },
      {
        id: "a-2",
        actor_email: "bob@acme.io",
        action: "user.invite",
        target_type: "user",
        target_id: "u-9",
        result: "success",
        metadata: {},
        created_at: "2026-07-06T12:02:00Z",
      },
      {
        id: "a-3",
        actor_email: "carol@acme.io",
        action: "key.delete",
        target_type: "api_key",
        target_id: "k-2",
        result: "success",
        metadata: {},
        created_at: "2026-07-06T12:01:00Z",
      },
    ],
    total: 3,
  };
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderActivity(tenantId: string = TENANT_ID, client: QueryClient = makeQueryClient()) {
  return render(
    <QueryClientProvider client={client}>
      <PlatformActivityTab tenantId={tenantId} />
    </QueryClientProvider>,
  );
}

// ── M8: own query, keyed by tenantId + offset ────────────────────────────────
describe("PlatformActivityTab — own query (M8)", () => {
  it("test_own_query_requests_the_path_scoped_audit_endpoint_at_default_offset", async () => {
    let capturedUrl: URL | null = null;
    server.use(
      http.get(AUDIT_PATH, ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(threeRows());
      }),
    );

    renderActivity();

    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(capturedUrl).not.toBeNull();
    expect(capturedUrl!.searchParams.get("offset")).toBe("0");
    expect(capturedUrl!.searchParams.get("limit")).toBe("20");
  });

  it("test_query_failure_renders_shared_error_state_with_retry", async () => {
    let calls = 0;
    server.use(
      http.get(AUDIT_PATH, () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json(
            { title: "Internal Server Error", status: 500 },
            { status: 500 },
          );
        }
        return HttpResponse.json(threeRows());
      }),
    );

    renderActivity();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/internal server error/i);
    });

    const retryButton = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(calls).toBe(2);
  });
});

// ── M12: loading state ───────────────────────────────────────────────────────
describe("PlatformActivityTab — loading state (M12)", () => {
  it("test_in_flight_query_renders_shared_loading_primitive", async () => {
    server.use(
      http.get(AUDIT_PATH, async () => {
        await delay(50);
        return HttpResponse.json(threeRows());
      }),
    );

    renderActivity();

    expect(screen.getByRole("status")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

// ── M9: Previous/Next pagination chrome ──────────────────────────────────────
describe("PlatformActivityTab — pagination chrome (M9)", () => {
  it("test_next_and_previous_page_the_server_driven_offset_and_disable_at_both_ends", async () => {
    const page0 = {
      items: Array.from({ length: 20 }, (_, i) => ({
        id: `page0-${i}`,
        actor_email: `user${i}@acme.io`,
        action: "key.create",
        target_type: "api_key",
        target_id: `k-${i}`,
        result: "success",
        metadata: {},
        created_at: "2026-07-06T12:00:00Z",
      })),
      total: 25,
    };
    const page1 = {
      items: Array.from({ length: 5 }, (_, i) => ({
        id: `page1-${i}`,
        actor_email: `later${i}@acme.io`,
        action: "key.delete",
        target_type: "api_key",
        target_id: `k2-${i}`,
        result: "success",
        metadata: {},
        created_at: "2026-07-06T11:00:00Z",
      })),
      total: 25,
    };
    server.use(
      http.get(AUDIT_PATH, ({ request }) => {
        const offset = new URL(request.url).searchParams.get("offset");
        return HttpResponse.json(offset === "20" ? page1 : page0);
      }),
    );

    renderActivity();

    await waitFor(() => {
      expect(screen.getByText(/user0@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/showing 1.*25/i)).toBeInTheDocument();

    const previousButton = screen.getByRole("button", { name: /previous/i });
    const nextButton = screen.getByRole("button", { name: /next/i });
    expect(previousButton).toBeDisabled();
    expect(nextButton).not.toBeDisabled();

    fireEvent.click(nextButton);
    await waitFor(() => {
      expect(screen.getByText(/later0@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/showing 21.*25/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /previous/i })).not.toBeDisabled();
    // offset(20) + items.length(5) = 25 >= total(25) -> Next disabled
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /previous/i }));
    await waitFor(() => {
      expect(screen.getByText(/user0@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
  });
});

// ── M10: shared DataTable with the expected columns ──────────────────────────
describe("PlatformActivityTab — table rendering (M10)", () => {
  it("test_rows_render_via_shared_datatable_with_actor_action_target_result_when_columns", async () => {
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json(threeRows())));

    renderActivity();

    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    const table = screen.getByRole("table", { name: /activity/i });
    ["Actor", "Action", "Target", "Result", "When"].forEach((col) => {
      expect(within(table).getByText(col)).toBeInTheDocument();
    });
    expect(within(table).getByText("key.create")).toBeInTheDocument();
    // "When" is formatted via the shared formatTimestamp, never a raw ISO string.
    expect(within(table).queryByText("2026-07-06T12:03:00Z")).not.toBeInTheDocument();
  });
});

// ── M11: zero audit history -> shared Empty state ────────────────────────────
describe("PlatformActivityTab — empty state (M11)", () => {
  it("test_zero_total_renders_shared_empty_state_no_table_no_pagination", async () => {
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json({ items: [], total: 0 })));

    renderActivity();

    await waitFor(() => {
      expect(screen.getByText(/no audit events yet/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /previous/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
  });
});

// ── M13: null actor/target fields render gracefully ──────────────────────────
describe("PlatformActivityTab — null fields (M13)", () => {
  it("test_null_actor_email_and_target_type_render_placeholder_not_crash", async () => {
    server.use(
      http.get(AUDIT_PATH, () =>
        HttpResponse.json({
          items: [
            {
              id: "sys-1",
              actor_email: null,
              action: "system.sweep",
              target_type: null,
              target_id: null,
              result: "success",
              metadata: {},
              created_at: "2026-07-06T12:00:00Z",
            },
          ],
          total: 1,
        }),
      ),
    );

    renderActivity();

    await waitFor(() => {
      expect(screen.getByText("system.sweep")).toBeInTheDocument();
    });
    const table = screen.getByRole("table", { name: /activity/i });
    const row = within(table).getByText("system.sweep").closest("tr")!;
    // every other field on the same row still renders normally (action, result)
    expect(within(row).getByText("success")).toBeInTheDocument();
    // null actor_email/target_type show a plain placeholder, never a blank crash cell
    const placeholders = within(row).getAllByText("—");
    expect(placeholders.length).toBeGreaterThanOrEqual(2);
  });
});

// ── R1: no mutating action anywhere in the tab ───────────────────────────────
describe("PlatformActivityTab — reject mutating actions (R1)", () => {
  it("test_reject_no_delete_redact_replay_or_export_control_exists", async () => {
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json(threeRows())));
    renderActivity();
    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });

    const forbidden = /delete|redact|replay|export|acknowledge/i;
    screen.getAllByRole("button").forEach((btn) => {
      expect(btn.textContent ?? "").not.toMatch(forbidden);
      expect(btn.getAttribute("aria-label") ?? "").not.toMatch(forbidden);
    });
  });
});

// ── R2: never a cross-tenant feed — only the path tenant's own rows ──────────
describe("PlatformActivityTab — reject cross-tenant feed (R2)", () => {
  it("test_reject_only_requests_the_single_given_tenant_never_another", async () => {
    let requestedPath = "";
    server.use(
      http.get(AUDIT_PATH, ({ request }) => {
        requestedPath = new URL(request.url).pathname;
        return HttpResponse.json(threeRows());
      }),
    );

    renderActivity(TENANT_ID);

    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(requestedPath).toBe(`/api/gw/admin/platform/tenants/${TENANT_ID}/audit`);
    expect(requestedPath).not.toContain(OTHER_TENANT_ID);
  });
});

// ── R3: no "last used at" concept anywhere ───────────────────────────────────
describe("PlatformActivityTab — reject last-used-at (R3)", () => {
  it("test_reject_no_last_used_at_field_or_column_anywhere", async () => {
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json(threeRows())));
    renderActivity();
    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/last used/i)).not.toBeInTheDocument();
  });
});

// ── R4: no bulk actions ───────────────────────────────────────────────────────
describe("PlatformActivityTab — reject bulk actions (R4)", () => {
  it("test_reject_no_select_all_or_bulk_control_exists", async () => {
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json(threeRows())));
    renderActivity();
    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/select all/i)).not.toBeInTheDocument();
  });
});

// ── R5: no saved views or filters ────────────────────────────────────────────
describe("PlatformActivityTab — reject saved views/filters (R5)", () => {
  it("test_reject_no_filter_daterange_or_saved_view_control_exists", async () => {
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json(threeRows())));
    renderActivity();
    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/filter/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/date range/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/saved view/i)).not.toBeInTheDocument();
  });
});

// ── R9: never client-side-only pagination ────────────────────────────────────
describe("PlatformActivityTab — reject client-side pagination (R9)", () => {
  it("test_reject_no_pagesizeoptions_rows_per_page_control_from_datatable", async () => {
    // A "Rows per page" control only ever renders when DataTable is given its own
    // pageSizeOptions prop (components/ui/data-table.tsx's own conditional render) — its
    // absence here is a direct, DOM-observable proxy for "pageSizeOptions was never passed."
    server.use(http.get(AUDIT_PATH, () => HttpResponse.json(threeRows())));
    renderActivity();
    await waitFor(() => {
      expect(screen.getByText(/alice@acme\.io/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/rows per page/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/rows per page/i)).not.toBeInTheDocument();
  });
});
