/**
 * tests-bff/model-catalog-paging-search.test.tsx — RED for v54 · #2.
 *
 * Adds client pagination + Fuse.js fuzzy search to the Models catalog via opt-in
 * DataTable props (default-off → the other 4 DataTable callers stay byte-identical).
 *
 * RED before build: today's ModelsPage renders ALL rows with no search box and no
 * pager, so the bounded-page / "Page X of Y" / search assertions fail (behavioral red);
 * the DataTable opt-in props don't exist yet either.
 *
 * Runs in the "bff" vitest project (msw same-origin handlers).
 */
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import React from "react";

import { ModelsPage } from "@/components/models/ModelsPage";
import { DataTable } from "@/components/ui";
import { UsageTable } from "@/components/usage/UsageTable";
import { AlertsTable } from "@/components/alerts/AlertsTable";
import { server } from "./mocks/server";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

const SONNET = {
  id: "anthropic/claude-3-5-sonnet",
  name: "Claude 3.5 Sonnet",
  context_length: 200000,
  enabled: false,
};
// 59 generic models + Sonnet = 60 total → 3 pages at size 25, 2 pages at size 50.
const GENERIC = Array.from({ length: 59 }, (_, i) => ({
  id: `prov/model-${i + 1}`,
  name: `Model ${i + 1}`,
  context_length: (i + 1) * 1000,
  enabled: i % 2 === 0,
}));
const SIXTY = [SONNET, ...GENERIC];

function listResponse(models: unknown[]) {
  return { object: "list", data: models };
}
function serveCatalog(models: unknown[]) {
  server.use(
    http.get(`${APP}/api/gw/admin/models`, () => HttpResponse.json(listResponse(models))),
  );
}
/** model rows == number of enable Switches rendered (one per visible model row). */
function rowCount() {
  return screen.queryAllByRole("switch").length;
}

describe("ModelsPage — pagination", () => {
  it("test_bounded_pages: shows at most one page of rows + page status", async () => {
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");
    expect(rowCount()).toBeLessThanOrEqual(25);
    expect(screen.getByText(/Page 1 of 3/i)).toBeInTheDocument();
  });

  it("test_next_prev: Next/Previous move between page slices", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText(/Page 2 of 3/i)).toBeInTheDocument();
    expect(rowCount()).toBeLessThanOrEqual(25);

    await user.click(screen.getByRole("button", { name: /previous/i }));
    expect(await screen.findByText(/Page 1 of 3/i)).toBeInTheDocument();
  });

  it("test_pager_bounds: Previous disabled on first page, Next disabled on last", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText(/Page 3 of 3/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("test_change_page_size: selecting 50 re-bounds rows and resets to page 1", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    await user.selectOptions(
      screen.getByRole("combobox", { name: /rows per page/i }),
      "50",
    );
    await waitFor(() => expect(screen.getByText(/Page 1 of 2/i)).toBeInTheDocument());
    expect(rowCount()).toBeLessThanOrEqual(50);
    expect(rowCount()).toBeGreaterThan(25);
  });
});

describe("ModelsPage — fuzzy search", () => {
  it("test_search_name_resets_page: filtering narrows rows and returns to page 1", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    // go to page 2 first
    await user.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText(/Page 2 of 3/i);

    await user.type(screen.getByRole("textbox", { name: /search models/i }), "claude");

    expect(await screen.findByText("Claude 3.5 Sonnet")).toBeInTheDocument();
    expect(screen.queryByText("Model 30")).not.toBeInTheDocument();
    expect(screen.getByText(/Page 1 of 1/i)).toBeInTheDocument();
  });

  it("test_search_typo_tolerant: a typo still matches (Fuse.js)", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    await user.type(screen.getByRole("textbox", { name: /search models/i }), "sonet");
    expect(await screen.findByText("Claude 3.5 Sonnet")).toBeInTheDocument();
  });

  it("test_clear_restores: clearing the query restores the full paged set", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    const box = screen.getByRole("textbox", { name: /search models/i });
    await user.type(box, "claude");
    await screen.findByText(/Page 1 of 1/i);
    await user.clear(box);

    expect(await screen.findByText(/Page 1 of 3/i)).toBeInTheDocument();
  });

  it("test_no_match_empty: zero matches shows Empty, search box retained, no rows", async () => {
    const user = userEvent.setup();
    serveCatalog(SIXTY);
    render(<ModelsPage />, { wrapper: Wrapper });
    await screen.findByText("Claude 3.5 Sonnet");

    // spy: a no-match state must NOT fire any enable/disable PUT (no row → no toggle target)
    let putFired = false;
    server.use(
      http.put(`${APP}/api/gw/admin/models/:id`, () => {
        putFired = true;
        return HttpResponse.json({});
      }),
    );

    await user.type(
      screen.getByRole("textbox", { name: /search models/i }),
      "zzzznotamodel",
    );
    expect(await screen.findByText(/no models match/i)).toBeInTheDocument();
    expect(rowCount()).toBe(0);
    // the search box stays so the user can correct the query
    expect(screen.getByRole("textbox", { name: /search models/i })).toBeInTheDocument();
    // server state untouched: no mutation was fired
    expect(putFired).toBe(false);
  });
});

describe("DataTable — paging+search are opt-in (other tables byte-identical)", () => {
  const columns = [{ accessorKey: "name", header: "Name" }];
  const data = [{ name: "Alpha" }, { name: "Beta" }];

  it("test_other_tables_unchanged: default render has NO search box, NO pager, NO page-size", () => {
    render(<DataTable columns={columns} data={data} />);
    expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /previous/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /rows per page/i })).not.toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });

  it("test_real_callers_unchanged: UsageTable + AlertsTable show no new controls (byte-identical)", () => {
    // The actual callsite wrappers must NOT have opted into the new props.
    const { unmount } = render(
      <UsageTable
        isLoading={false}
        isError={false}
        data={{
          total_cost_usd: "0.01",
          total_requests: 1,
          total_prompt_tokens: 1,
          total_completion_tokens: 1,
          records: [
            {
              id: "u1",
              model_id: "openai/gpt-4o",
              prompt_tokens: 1,
              completion_tokens: 1,
              cost_usd: "0.01",
              status: 200,
              created_at: "2026-06-10T00:00:00Z",
            },
          ],
        }}
      />,
    );
    expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /rows per page/i })).not.toBeInTheDocument();
    unmount();

    render(
      <AlertsTable
        data={{
          items: [
            {
              id: "a1",
              event_type: "budget_threshold_reached",
              payload: { pct: 80 },
              created_at: "2026-06-10T00:00:00Z",
              delivered: false,
              delivered_at: null,
            },
          ],
          total: 1,
        }}
      />,
    );
    expect(screen.queryByRole("textbox", { name: /search/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /next/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /rows per page/i })).not.toBeInTheDocument();
  });

  it("test_optin_renders_controls: searchable + pageSizeOptions render the controls", () => {
    render(
      <DataTable
        columns={columns}
        data={data}
        searchable
        searchPlaceholder="Search…"
        searchKeys={["name"]}
        pageSizeOptions={[25, 50, 100]}
      />,
    );
    expect(screen.getByRole("textbox", { name: /search/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /rows per page/i })).toBeInTheDocument();
  });
});
