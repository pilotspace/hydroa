/**
 * tests/billing-invoices.test.tsx — RED suite for the Invoices list + detail + evidence
 * drawer + export (billing-ui TASK.md §3 — FROZEN @ v1, M2/M3/M4/M5/M9/M10/M11/M12,
 * R1/R2/R3/R4).
 *
 * Mirrors tests/logs.test.tsx's own convention: fresh QueryClientProvider per render, msw
 * per-test overrides, within(section())-scoped assertions, axe sweep.
 *
 * RED before Build: `@/components/invoices/InvoicesListPage` and
 * `@/components/invoices/InvoiceDetailPage` do not exist yet -> MODULE_NOT_FOUND, the
 * established true-red convention (see tests/logs.test.tsx's own header comment).
 */

import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import { axe } from "@/test-support/axe";
import React from "react";

import { InvoicesListPage } from "@/components/invoices/InvoicesListPage";
import { InvoiceDetailPage } from "@/components/invoices/InvoiceDetailPage";

const APP = "http://localhost:3000";
const INVOICES_URL = `${APP}/api/gw/admin/invoices`;
const detailUrl = (id: string) => `${APP}/api/gw/admin/invoices/${id}`;
const evidenceUrl = (id: string, lineId: string) =>
  `${APP}/api/gw/admin/invoices/${id}/lines/${lineId}/evidence`;

const INVOICE_ID = "aaaaaaaa-0000-0000-0000-000000000001";
const LINE_ID = "bbbbbbbb-0000-0000-0000-000000000001";

const INVOICE_ITEM_ISSUED = {
  id: INVOICE_ID,
  period_start: "2026-06-01T00:00:00Z",
  period_end: "2026-07-01T00:00:00Z",
  status: "issued",
  total_usd: "1204.55",
  currency: "USD",
  issued_at: "2026-07-03T00:00:00Z",
};
const INVOICE_ITEM_DRAFT = {
  id: "aaaaaaaa-0000-0000-0000-000000000002",
  period_start: "2026-07-01T00:00:00Z",
  period_end: "2026-08-01T00:00:00Z",
  status: "draft",
  total_usd: "312.40",
  currency: "USD",
  issued_at: null,
};

const INVOICES_RESPONSE = { items: [INVOICE_ITEM_ISSUED, INVOICE_ITEM_DRAFT], next_cursor: null, has_more: false };
const INVOICES_EMPTY_RESPONSE = { items: [], next_cursor: null, has_more: false };

const INVOICE_LINE = {
  id: LINE_ID,
  model_id: "gpt-4o-mini",
  team_id: null,
  key_id: "k_a1",
  tags: {},
  amount_usd: "1204.55",
  prompt_tokens: 40000,
  completion_tokens: 12000,
  request_count: 812,
  line_type: "usage",
};

const INVOICE_DETAIL_ISSUED = {
  ...INVOICE_ITEM_ISSUED,
  tenant_id: "tenant-1",
  raw_total_usd: "1204.55123456",
  tax_usd: "0.00",
  corrected_total_usd: "1204.55",
  created_at: "2026-07-03T00:00:00Z",
  lines: [INVOICE_LINE],
  corrections: [],
};

const INVOICE_DETAIL_DRAFT = { ...INVOICE_DETAIL_ISSUED, ...INVOICE_ITEM_DRAFT, corrections: [] };

const INVOICE_DETAIL_WITH_CORRECTION = {
  ...INVOICE_DETAIL_ISSUED,
  corrected_total_usd: "1192.05",
  corrections: [
    { id: "c1", delta_usd: "-12.50", reason: "duplicate line", created_by: "ops@tenant.io", created_at: "2026-06-14T00:00:00Z" },
  ],
};

const EVIDENCE_ITEM = {
  usage_record_id: "ur-1",
  created_at: "2026-06-10T10:02:14Z",
  model_id: "gpt-4o-mini",
  prompt_tokens: 512,
  completion_tokens: 128,
  cost_usd: "0.021",
  request_id: "req_9f3c",
};
const EVIDENCE_RESPONSE = { items: [EVIDENCE_ITEM], next_cursor: null, has_more: false };

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}
function renderList() {
  return render(<InvoicesListPage />, { wrapper: Wrapper });
}
function renderDetail(invoiceId: string = INVOICE_ID) {
  return render(<InvoiceDetailPage invoiceId={invoiceId} />, { wrapper: Wrapper });
}
function section() {
  return screen.getByRole("region");
}

beforeEach(() => {
  getRouterMock().push.mockClear();
});

// ── M2/M9/R1/R2: Invoices list ──────────────────────────────────────────────

describe("InvoicesListPage — list (M2, M9)", () => {
  it("test_list_renders_keyset_paginated_table_and_row_click_navigates", async () => {
    server.use(http.get(INVOICES_URL, () => HttpResponse.json(INVOICES_RESPONSE)));
    const user = userEvent.setup();
    renderList();

    await waitFor(() => expect(within(section()).getByText("$1,204.55")).toBeInTheDocument());
    expect(within(section()).getByText("Issued")).toBeInTheDocument();
    expect(within(section()).getByText("Draft")).toBeInTheDocument();

    const row = within(section()).getByRole("row", { name: /1,204\.55/ });
    await user.click(row);
    expect(getRouterMock().push).toHaveBeenCalledWith(`/app/invoices/${INVOICE_ID}`);
  });

  it("test_next_previous_mirrors_the_cursor_stack_idiom", async () => {
    const PAGE1 = { items: [INVOICE_ITEM_ISSUED], next_cursor: "cursor-abc", has_more: true };
    const PAGE2 = { items: [INVOICE_ITEM_DRAFT], next_cursor: null, has_more: false };
    server.use(
      http.get(INVOICES_URL, ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(url.searchParams.get("cursor") === "cursor-abc" ? PAGE2 : PAGE1);
      }),
    );
    const user = userEvent.setup();
    renderList();

    await waitFor(() => expect(within(section()).getByText("$1,204.55")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /^next$/i }));
    await waitFor(() => expect(within(section()).getByText("$312.40")).toBeInTheDocument());
    expect(within(section()).getByRole("button", { name: /^previous$/i })).toBeEnabled();
    await user.click(within(section()).getByRole("button", { name: /^previous$/i }));
    await waitFor(() => expect(within(section()).getByText("$1,204.55")).toBeInTheDocument());
  });

  it("test_empty_invoices_list_shows_informative_empty_state", async () => {
    server.use(http.get(INVOICES_URL, () => HttpResponse.json(INVOICES_EMPTY_RESPONSE)));
    renderList();

    await waitFor(() => expect(within(section()).getByText(/no invoices yet/i)).toBeInTheDocument());
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
    expect(within(section()).queryByRole("button", { name: /^next$/i })).not.toBeInTheDocument();
  });

  it("test_forbidden_access_shows_the_shared_inline_message", async () => {
    // Covers R1 (member direct-URL) and R2 (operator via shown nav link) identically —
    // the component cannot distinguish the two; both are just "the API returned 403".
    server.use(
      http.get(INVOICES_URL, () =>
        HttpResponse.json({ title: "Insufficient role", status: 403, code: "ERR_AUTH_FORBIDDEN" }, { status: 403 }),
      ),
    );
    renderList();

    await waitFor(() => expect(within(section()).getByText(/you don't have access to invoices/i)).toBeInTheDocument());
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
  });

  it("test_axe_no_serious_violations", async () => {
    server.use(http.get(INVOICES_URL, () => HttpResponse.json(INVOICES_RESPONSE)));
    const { container } = renderList();
    await waitFor(() => expect(within(section()).getByText("$1,204.55")).toBeInTheDocument());
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});

// ── M3/M10: Invoice detail ──────────────────────────────────────────────────

describe("InvoiceDetailPage — visibly-immutable financial-document idiom (M3, M10)", () => {
  it("test_issued_invoice_renders_visibly_immutable_seal_and_no_edit_affordance", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("Issued")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^edit$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("test_draft_invoice_renders_a_distinct_non_final_seal", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_DRAFT)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText(/draft/i)).toBeInTheDocument());
    expect(within(section()).queryByText(/^issued$/i)).not.toBeInTheDocument();
  });

  it("test_lines_render_grouped_tabular_nums_amounts_tracing_to_the_total", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    expect(within(section()).getByText("k_a1")).toBeInTheDocument();
    // the line amount and the footer total are both rendered verbatim from the API (never
    // re-summed client-side) — here they happen to be equal (one line == the whole invoice).
    expect(within(section()).getAllByText("$1,204.55").length).toBeGreaterThanOrEqual(2);
  });

  it("test_subtotal_and_tax_render_from_the_payload (audit-remediation)", async () => {
    // DEFECT: tax_usd and raw_total_usd are present on the GET /admin/invoices/{id}
    // payload (and the InvoiceDetail interface already types them) but were dropped
    // from the rendered UI — only total_usd ever reached the page. Distinct values
    // from total_usd here (unlike the shared fixture, where they coincide) so the
    // assertions can't pass by accident.
    const DETAIL_WITH_TAX = {
      ...INVOICE_DETAIL_ISSUED,
      raw_total_usd: "1150.00",
      tax_usd: "54.55",
      total_usd: "1204.55",
    };
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(DETAIL_WITH_TAX)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    expect(within(section()).getByText("$1,150.00")).toBeInTheDocument();
    expect(within(section()).getByText("$54.55")).toBeInTheDocument();
    // total_usd "1204.55" happens to equal the single line's own amount_usd
    // (same coincidence the pre-existing suite already tolerates at line ~220) —
    // both the line cell and the Total footer legitimately render "$1,204.55".
    expect(within(section()).getAllByText("$1,204.55").length).toBeGreaterThanOrEqual(1);
  });

  it("test_zero_tax_and_null_safe_rendering (audit-remediation)", async () => {
    // A missing/zero tax must render cleanly (never "NaN"/"undefined") — the
    // shared fixture's tax_usd is the string "0.00".
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    expect(within(section()).getByText("$0.00")).toBeInTheDocument();
    expect(within(section()).queryByText(/nan/i)).not.toBeInTheDocument();
    expect(within(section()).queryByText(/undefined/i)).not.toBeInTheDocument();
  });

  it("test_corrections_render_as_signed_deltas_never_mutating_original_lines", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_WITH_CORRECTION)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText(/duplicate line/i)).toBeInTheDocument());
    expect(within(section()).getByText(/ops@tenant\.io/)).toBeInTheDocument();
    expect(within(section()).getByText(/\$12\.50/)).toBeInTheDocument();
    // the original line is unaffected
    expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(within(section()).getByText("$1,192.05")).toBeInTheDocument();
  });

  it("test_zero_corrections_shows_its_own_empty_state_not_an_omitted_section", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText(/no corrections/i)).toBeInTheDocument());
  });

  it("test_unknown_or_cross_tenant_invoice_id_shows_not_found", async () => {
    server.use(
      http.get(detailUrl(INVOICE_ID), () =>
        HttpResponse.json({ title: "Invoice not found", status: 404, code: "ERR_INVOICE_NOT_FOUND" }, { status: 404 }),
      ),
    );
    renderDetail();

    await waitFor(() => expect(within(section()).getByText(/invoice not found/i)).toBeInTheDocument());
  });

  it("test_axe_no_serious_violations", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    const { container } = renderDetail();
    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toHaveLength(0);
  });
});

// ── M4/R3: Evidence drawer ───────────────────────────────────────────────────

describe("InvoiceDetailPage — evidence drawer (M4, R3)", () => {
  it("test_opening_a_lines_evidence_action_fetches_and_lists_usage_rows", async () => {
    server.use(
      http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)),
      http.get(evidenceUrl(INVOICE_ID, LINE_ID), () => HttpResponse.json(EVIDENCE_RESPONSE)),
    );
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /view evidence/i }));

    await waitFor(() => expect(screen.getByText(/req_9f3c/)).toBeInTheDocument());
    expect(screen.getByText("$0.02")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /logs/i })).not.toBeInTheDocument();
  });

  it("test_drawer_paginates_without_closing", async () => {
    const PAGE1 = { items: [EVIDENCE_ITEM], next_cursor: "cur-2", has_more: true };
    const PAGE2 = { items: [{ ...EVIDENCE_ITEM, usage_record_id: "ur-2", request_id: "req_a01e" }], next_cursor: null, has_more: false };
    server.use(
      http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)),
      http.get(evidenceUrl(INVOICE_ID, LINE_ID), ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(url.searchParams.get("cursor") === "cur-2" ? PAGE2 : PAGE1);
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /view evidence/i }));
    await waitFor(() => expect(screen.getByText(/req_9f3c/)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /^next$/i }));
    await waitFor(() => expect(screen.getByText(/req_a01e/)).toBeInTheDocument());
    // still open — the underlying invoice detail is unaffected (queried by text, not role:
    // Radix's own inert-background makes the section temporarily inaccessible-by-role while
    // the modal drawer is open — same idiom as tests/logs.test.tsx's own precedent). Both the
    // line's own Model cell AND the evidence row's Model column legitimately read
    // "gpt-4o-mini" here — assert at least one instance survived, not exactly one.
    expect(screen.getAllByText("gpt-4o-mini").length).toBeGreaterThanOrEqual(2);
  });

  it("test_closing_the_drawer_returns_focus_to_the_triggering_control", async () => {
    server.use(
      http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)),
      http.get(evidenceUrl(INVOICE_ID, LINE_ID), () => HttpResponse.json(EVIDENCE_RESPONSE)),
    );
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    const trigger = within(section()).getByRole("button", { name: /view evidence/i });
    await user.click(trigger);
    await waitFor(() => expect(screen.getByText(/req_9f3c/)).toBeInTheDocument());

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText(/req_9f3c/)).not.toBeInTheDocument());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("test_unknown_line_id_shows_not_found_inside_drawer_detail_page_unaffected", async () => {
    server.use(
      http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)),
      http.get(evidenceUrl(INVOICE_ID, LINE_ID), () =>
        HttpResponse.json({ title: "Invoice not found", status: 404, code: "ERR_INVOICE_NOT_FOUND" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /view evidence/i }));

    await waitFor(() => expect(screen.getByText(/evidence not found/i)).toBeInTheDocument());
    expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument();
  });
});

// ── M5/R4: Export ─────────────────────────────────────────────────────────────

describe("InvoiceDetailPage — export (M5, R4)", () => {
  it("test_export_links_point_at_the_binary_passthrough_route_zero_new_bff_code", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    const pdf = within(section()).getByRole("link", { name: /download pdf/i });
    const csv = within(section()).getByRole("link", { name: /download csv/i });
    expect(pdf).toHaveAttribute("href", `/api/gw/admin/invoices/${INVOICE_ID}/export?format=pdf`);
    expect(pdf).toHaveAttribute("download");
    expect(csv).toHaveAttribute("href", `/api/gw/admin/invoices/${INVOICE_ID}/export?format=csv`);
    expect(csv).toHaveAttribute("download");
  });

  it("test_export_hrefs_never_carry_any_format_other_than_pdf_or_csv", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    const links = within(section())
      .getAllByRole("link")
      .filter((l) => l.getAttribute("href")?.includes("/export"));
    expect(links.length).toBe(2);
    for (const link of links) {
      const href = link.getAttribute("href") ?? "";
      const format = new URL(href, "http://x").searchParams.get("format");
      expect(["pdf", "csv"]).toContain(format);
    }
  });
});

// ── M12: Responsive ────────────────────────────────────────────────────────────

describe("InvoiceDetailPage — responsive (M12)", () => {
  it("test_lines_table_scrolls_horizontally_inside_its_own_container", async () => {
    server.use(http.get(detailUrl(INVOICE_ID), () => HttpResponse.json(INVOICE_DETAIL_ISSUED)));
    renderDetail();

    await waitFor(() => expect(within(section()).getByText("gpt-4o-mini")).toBeInTheDocument());
    const table = within(section()).getByRole("table", { name: /invoice lines/i });
    // Table primitive wraps itself in a `relative w-full overflow-auto` div (table.tsx) —
    // the same convention LogsTable/AuditTable rely on; assert the wrapper is present.
    expect(table.parentElement).toHaveClass("overflow-auto");
  });
});
