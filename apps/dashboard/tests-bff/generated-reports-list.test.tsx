/**
 * tests-bff/generated-reports-list.test.tsx — RED/GREEN suite for
 * components/compliance/GeneratedReportsList.tsx (compliance-report-center
 * TASK.md §3 CONTRACT — FROZEN @ v1, M18/R11/R13).
 *
 * Keyset-paginated (useInfiniteQuery) inbox of compliance_report_runs rows.
 * Each row's download intercepts the anchor's primary click to `fetch` the BFF
 * pass-through path directly (raw `fetch`, not bffGet — the response is a binary
 * download, not JSON) so a 503/404 can render a ROW-level error without blanking
 * the rest of the list; a 2xx triggers a Blob download via a temporary anchor.
 *
 * RED failure mode: import from @/components/compliance/GeneratedReportsList
 * fails with MODULE_NOT_FOUND until Build writes the component.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { GeneratedReportsList } from "@/components/compliance/GeneratedReportsList";
import { expectNoSeriousViolations } from "@/test-support/axe";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ title, status, code }, { status });
}

const REPORTS_EMPTY = { items: [] as unknown[], next_cursor: null as string | null, has_more: false };

const REPORT_1 = {
  id: "11111111-1111-1111-1111-111111111111",
  period_start: "2026-05-01T00:00:00Z",
  period_end: "2026-05-31T23:59:59Z",
  generated_at: "2026-06-01T00:05:00Z",
  size_bytes: 20480,
  format_version: "1",
};

const REPORT_2 = {
  id: "22222222-2222-2222-2222-222222222222",
  period_start: "2026-04-01T00:00:00Z",
  period_end: "2026-04-30T23:59:59Z",
  generated_at: "2026-05-01T00:05:00Z",
  size_bytes: 15000,
  format_version: "1",
};

const reportsGet = (body: unknown = REPORTS_EMPTY) =>
  http.get(`${APP}/api/gw/admin/compliance/reports`, () =>
    HttpResponse.json(body as Parameters<typeof HttpResponse.json>[0]),
  );

// jsdom does not implement Blob URLs or <a download> navigation — stub the
// pieces the component touches directly so the Blob-download path is
// observable without a real browser.
const originalCreateObjectURL = URL.createObjectURL;
const originalRevokeObjectURL = URL.revokeObjectURL;

afterEach(() => {
  URL.createObjectURL = originalCreateObjectURL;
  URL.revokeObjectURL = originalRevokeObjectURL;
  vi.restoreAllMocks();
});

describe("GeneratedReportsList — list rendering", () => {
  it("test_loading_then_empty_state", async () => {
    server.use(reportsGet(REPORTS_EMPTY));
    render(<GeneratedReportsList />, { wrapper: Wrapper });
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByText(/no reports generated yet/i)).toBeInTheDocument();
  });

  it("test_list_error_shows_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/compliance/reports`, () =>
        problem("Server error", 500, "ERR_INTERNAL"),
      ),
    );
    render(<GeneratedReportsList />, { wrapper: Wrapper });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("test_renders_rows_period_generated_size_download", async () => {
    server.use(reportsGet({ items: [REPORT_1, REPORT_2], next_cursor: null, has_more: false }));
    render(<GeneratedReportsList />, { wrapper: Wrapper });

    const rows = await screen.findAllByRole("row");
    // header row + 2 data rows
    expect(rows).toHaveLength(3);
    expect(screen.getByText(/20,480 bytes/)).toBeInTheDocument();
    expect(screen.getByText(/15,000 bytes/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /download/i })).toHaveLength(2);
  });

  it("test_load_more_pagination_appends_rows", async () => {
    const user = userEvent.setup();
    let callCount = 0;
    server.use(
      http.get(`${APP}/api/gw/admin/compliance/reports`, ({ request }) => {
        callCount += 1;
        const url = new URL(request.url);
        if (url.searchParams.get("cursor")) {
          return HttpResponse.json({ items: [REPORT_2], next_cursor: null, has_more: false });
        }
        return HttpResponse.json({
          items: [REPORT_1],
          next_cursor: "cursor-abc",
          has_more: true,
        });
      }),
    );
    render(<GeneratedReportsList />, { wrapper: Wrapper });

    await screen.findByText(/20,480 bytes/);
    const loadMore = screen.getByRole("button", { name: /load more/i });
    await user.click(loadMore);

    await waitFor(() => expect(screen.getByText(/15,000 bytes/)).toBeInTheDocument());
    expect(callCount).toBe(2);
    // no more pages — the affordance disappears
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });
});

describe("GeneratedReportsList — row download", () => {
  it("test_row_download_success_triggers_blob_download", async () => {
    const user = userEvent.setup();
    server.use(reportsGet({ items: [REPORT_1], next_cursor: null, has_more: false }));
    const blob = new Blob(["{}"], { type: "application/json" });
    server.use(
      http.get(`${APP}/api/gw/admin/compliance/reports/${REPORT_1.id}`, () =>
        HttpResponse.json(
          {},
          {
            headers: {
              "content-disposition": 'attachment; filename="art12-bundle-may.json"',
            },
          },
        ),
      ),
    );
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    render(<GeneratedReportsList />, { wrapper: Wrapper });
    const link = await screen.findByRole("link", { name: /download/i });
    await user.click(link);

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    // no row-level error rendered on success
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("test_row_download_503_shows_inline_row_error_list_not_blanked", async () => {
    const user = userEvent.setup();
    server.use(
      reportsGet({ items: [REPORT_1, REPORT_2], next_cursor: null, has_more: false }),
      http.get(`${APP}/api/gw/admin/compliance/reports/${REPORT_1.id}`, () =>
        problem("Object storage is unavailable", 503, "ERR_OBJECT_STORE_UNAVAILABLE"),
      ),
    );
    render(<GeneratedReportsList />, { wrapper: Wrapper });
    const links = await screen.findAllByRole("link", { name: /download/i });
    await user.click(links[0]);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/object storage is unavailable/i);
    // R13: the rest of the list stays mounted — not replaced wholesale.
    expect(screen.getByText(/20,480 bytes/)).toBeInTheDocument();
    expect(screen.getByText(/15,000 bytes/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /download/i })).toHaveLength(2);
  });

  it("test_row_download_404_shows_inline_row_error", async () => {
    const user = userEvent.setup();
    server.use(
      reportsGet({ items: [REPORT_1], next_cursor: null, has_more: false }),
      http.get(`${APP}/api/gw/admin/compliance/reports/${REPORT_1.id}`, () =>
        problem("Report not found", 404, "ERR_REPORT_NOT_FOUND"),
      ),
    );
    render(<GeneratedReportsList />, { wrapper: Wrapper });
    const link = await screen.findByRole("link", { name: /download/i });
    await user.click(link);

    expect(await screen.findByRole("alert")).toHaveTextContent(/not found/i);
    // the row itself is still present, still offering a download retry link.
    expect(screen.getByRole("link", { name: /download/i })).toBeInTheDocument();
  });
});

describe("GeneratedReportsList — accessibility", () => {
  it("test_axe_empty_state", async () => {
    server.use(reportsGet(REPORTS_EMPTY));
    const { container } = render(<GeneratedReportsList />, { wrapper: Wrapper });
    await screen.findByText(/no reports generated yet/i);
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_error_state", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/compliance/reports`, () =>
        problem("Server error", 500, "ERR_INTERNAL"),
      ),
    );
    const { container } = render(<GeneratedReportsList />, { wrapper: Wrapper });
    await screen.findByRole("alert");
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_populated_table_state", async () => {
    server.use(reportsGet({ items: [REPORT_1, REPORT_2], next_cursor: null, has_more: false }));
    const { container } = render(<GeneratedReportsList />, { wrapper: Wrapper });
    await screen.findByText(/20,480 bytes/);
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });
});
