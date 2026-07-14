/**
 * tests-bff/compliance-report-center.test.tsx — RED/GREEN suite for
 * components/compliance/ComplianceReportCenter.tsx (compliance-report-center
 * TASK.md §3 CONTRACT — FROZEN @ v1). Covers the on-demand generation half
 * (period picker -> assembleArt12Bundle walk -> preview/download) and its error
 * taxonomy; the schedule/reports halves have their own dedicated suites
 * (schedule-control.test.tsx, generated-reports-list.test.tsx).
 *
 * Every test must ALSO stub GET /admin/compliance/report-schedule and
 * GET /admin/compliance/reports (ComplianceReportCenter unconditionally mounts
 * <ScheduleControl/> and <GeneratedReportsList/> below the picker) — msw's
 * onUnhandledRequest:"error" (tests-bff/setup.ts) throws on any unstubbed call.
 *
 * RED failure mode: import from @/components/compliance/ComplianceReportCenter
 * fails with MODULE_NOT_FOUND until Build writes the component.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import React from "react";

import { ComplianceReportCenter } from "@/components/compliance/ComplianceReportCenter";
import type { Art12BundleResponse, CoverModel } from "@/lib/art12-bundle";
import { expectNoSeriousViolations } from "@/test-support/axe";

const APP = "http://localhost:3000";
const BUNDLE_PATH = `${APP}/api/gw/admin/compliance/art12-bundle`;

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

const SCHEDULE_DEFAULT = {
  enabled: false,
  cadence: "monthly",
  day_of_month: 1,
  window_policy: "previous_calendar_month",
  created_by: null as string | null,
  created_at: null as string | null,
  updated_at: null as string | null,
  last_run_at: null as string | null,
  last_run_status: null as string | null,
  next_run_at: null as string | null,
};
const REPORTS_EMPTY = { items: [] as unknown[], next_cursor: null as string | null, has_more: false };

/** Every test needs these two — ScheduleControl/GeneratedReportsList mount unconditionally. */
function baseHandlers() {
  return [
    http.get(`${APP}/api/gw/admin/compliance/report-schedule`, () =>
      HttpResponse.json(SCHEDULE_DEFAULT),
    ),
    http.get(`${APP}/api/gw/admin/compliance/reports`, () => HttpResponse.json(REPORTS_EMPTY)),
  ];
}

function makeCover(overrides: Partial<CoverModel> = {}): CoverModel {
  return {
    bundle_id: "bundle-xyz",
    generated_at: "2026-07-01T00:00:00Z",
    tenant_id: "00000000-0000-0000-0000-000000000099",
    tenant_name: "Acme",
    period: { since: "2026-06-01T00:00:00.000Z", until: "2026-06-30T00:00:00.000Z" },
    residency_pin: "eu",
    zdr_state: { enabled: false, enabled_at: null },
    retention_window_days: 90,
    guardrail_configs_snapshot: { prompt_injection: {} },
    default_tier: "standard",
    format_version: "1",
    ...overrides,
  };
}

function emptySection<T>(overrides: { items?: T[]; has_more?: boolean; note?: string | null } = {}) {
  return {
    items: (overrides.items ?? []) as T[],
    next_cursor: null,
    has_more: overrides.has_more ?? false,
    note: overrides.note ?? null,
  };
}

function makePage(overrides: Partial<Art12BundleResponse> = {}): Art12BundleResponse {
  return {
    cover: makeCover(),
    sections: {
      audit_events: emptySection(),
      request_log_metadata: emptySection(),
      usage_lineage: emptySection(),
    },
    bundle_token: null,
    ...overrides,
  };
}

async function fillPeriodAndGenerate(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/^from$/i), "2026-06-01T00:00");
  await user.type(screen.getByLabelText(/^to$/i), "2026-06-30T00:00");
  await user.click(screen.getByRole("button", { name: /^generate$/i }));
}

describe("ComplianceReportCenter — period picker", () => {
  it("test_picker_renders_and_generate_disabled_until_both_dates_set", async () => {
    server.use(...baseHandlers());
    render(<ComplianceReportCenter />, { wrapper: Wrapper });

    const from = screen.getByLabelText(/^from$/i);
    const to = screen.getByLabelText(/^to$/i);
    const generate = screen.getByRole("button", { name: /^generate$/i });
    expect(generate).toBeDisabled();

    await userEvent.type(from, "2026-06-01T00:00");
    expect(generate).toBeDisabled();
    await userEvent.type(to, "2026-06-30T00:00");
    expect(generate).toBeEnabled();
  });

  it("test_inputs_disabled_while_generating", async () => {
    const user = userEvent.setup();
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, async () => {
        await new Promise((r) => setTimeout(r, 30));
        return HttpResponse.json(makePage());
      }),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);

    expect(screen.getByLabelText(/^from$/i)).toBeDisabled();
    expect(screen.getByLabelText(/^to$/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /generating/i })).toBeDisabled();

    await waitFor(() => expect(screen.getByRole("button", { name: /^generate$/i })).toBeEnabled());
  });

  it("test_since_after_until_blocks_client_side_no_fetch", async () => {
    const user = userEvent.setup();
    let called = false;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => {
        called = true;
        return HttpResponse.json(makePage());
      }),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });

    await user.type(screen.getByLabelText(/^from$/i), "2026-06-30T00:00");
    await user.type(screen.getByLabelText(/^to$/i), "2026-06-01T00:00");
    await user.click(screen.getByRole("button", { name: /^generate$/i }));

    expect(await screen.findByText(/from must be on or before to/i)).toBeInTheDocument();
    expect(called).toBe(false);
  });
});

describe("ComplianceReportCenter — multi-page walk and preview", () => {
  it("test_generate_walks_three_pages_shows_cover_seal_and_verbatim_note", async () => {
    const user = userEvent.setup();
    let call = 0;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => {
        call += 1;
        if (call === 1) {
          return HttpResponse.json(
            makePage({
              sections: {
                audit_events: emptySection({
                  items: [{ id: "a1" }] as unknown as never[],
                  has_more: true,
                  note: "ZDR is enabled — request logs are unavailable",
                }),
                request_log_metadata: emptySection(),
                usage_lineage: emptySection(),
              },
              bundle_token: "tok-1",
            }),
          );
        }
        if (call === 2) {
          return HttpResponse.json(
            makePage({
              sections: {
                audit_events: emptySection({ items: [{ id: "a2" }] as unknown as never[], has_more: true }),
                request_log_metadata: emptySection(),
                usage_lineage: emptySection(),
              },
              bundle_token: "tok-2",
            }),
          );
        }
        return HttpResponse.json(
          makePage({
            sections: {
              audit_events: emptySection({ items: [{ id: "a3" }] as unknown as never[], has_more: false }),
              request_log_metadata: emptySection(),
              usage_lineage: emptySection(),
            },
            bundle_token: null,
          }),
        );
      }),
    );
    const { container } = render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);

    expect(await screen.findByText("bundle-xyz")).toBeInTheDocument();
    await waitFor(() => expect(call).toBe(3));

    // seal + cover fields — the dl's dt labels are EXACTLY the frozen set, no
    // invented field ("Compliant"/"Status"/etc never appear).
    expect(screen.getByText(/record-keeping bundle/i)).toBeInTheDocument();
    const dtLabels = Array.from(container.querySelectorAll("dt")).map((el) => el.textContent);
    expect(dtLabels).toEqual([
      "Bundle ID",
      "Generated at",
      "Tenant",
      "Period",
      "Residency pin",
      "Zero-Data-Retention",
      "Retention window",
      "Guardrail configs",
      "Default tier",
      "Format version",
    ]);
    expect(screen.getByText(/acme/i)).toBeInTheDocument();

    // 3 pages accumulated -> 3 audit events total
    expect(screen.getByText("3")).toBeInTheDocument();
    // the note from page 1 propagates verbatim
    expect(
      screen.getByText("ZDR is enabled — request logs are unavailable"),
    ).toBeInTheDocument();
  });

  it("test_copy_floor_scan_no_forbidden_compliance_claims", async () => {
    const user = userEvent.setup();
    server.use(...baseHandlers(), http.get(BUNDLE_PATH, () => HttpResponse.json(makePage())));
    const { container } = render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);
    await screen.findByText("bundle-xyz");

    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\bcompliant\b/i);
    expect(text.toLowerCase()).not.toContain("makes you compliant");
    expect(text).not.toMatch(/gpai compliance/i);
  });

  it("test_download_assembles_all_pages_with_correct_filename", async () => {
    const user = userEvent.setup();
    let call = 0;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => {
        call += 1;
        return HttpResponse.json(
          makePage({
            sections: {
              audit_events: emptySection({ items: [{ id: `a${call}` }] as unknown as never[], has_more: call < 2 }),
              request_log_metadata: emptySection(),
              usage_lineage: emptySection(),
            },
            bundle_token: call < 2 ? "tok-1" : null,
          }),
        );
      }),
    );
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    let capturedDownloadAttr: string | undefined;
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        capturedDownloadAttr = this.download;
      });
    // jsdom's Blob has no .text()/.arrayBuffer() — spy the constructor itself to
    // capture the raw parts array the component actually assembled.
    const OriginalBlob = globalThis.Blob;
    let capturedParts: BlobPart[] | undefined;
    const BlobSpy = vi.fn(function (this: unknown, parts: BlobPart[], options?: BlobPropertyBag) {
      capturedParts = parts;
      return new OriginalBlob(parts, options);
    }) as unknown as typeof Blob;
    globalThis.Blob = BlobSpy;

    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);
    await screen.findByText("bundle-xyz");

    const downloadBtn = screen.getByRole("button", { name: /download bundle/i });
    await user.click(downloadBtn);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(capturedParts).toBeDefined();
    const parsed = JSON.parse(String(capturedParts?.[0])) as {
      cover: { tenant_id: string };
      sections: { audit_events: { items: unknown[] } };
    };
    expect(parsed.sections.audit_events.items).toHaveLength(2);
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    // filename derives from the cover's tenant_id + period, date-only (YYYY-MM-DD).
    expect(capturedDownloadAttr).toBe(
      `art12-bundle-${parsed.cover.tenant_id}-2026-06-01-2026-06-30.json`,
    );

    globalThis.Blob = OriginalBlob;
  });
});

describe("ComplianceReportCenter — error taxonomy", () => {
  it("test_forbidden_replaces_whole_surface_no_form", async () => {
    const user = userEvent.setup();
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => problem("Owner or admin required", 403, "ERR_AUTH_FORBIDDEN")),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/don't have access/i);
    // whole surface replaced — no picker, no schedule control, no reports list.
    expect(screen.queryByLabelText(/^from$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/scheduled generation/i)).not.toBeInTheDocument();
  });

  it("test_401_mid_walk_discards_partial_state", async () => {
    const user = userEvent.setup();
    let call = 0;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => {
        call += 1;
        if (call === 1) {
          return HttpResponse.json(
            makePage({
              sections: {
                audit_events: emptySection({ items: [{ id: "a1" }] as unknown as never[], has_more: true }),
                request_log_metadata: emptySection(),
                usage_lineage: emptySection(),
              },
              bundle_token: "tok-1",
            }),
          );
        }
        // NOT ERR_AUTH_SESSION_EXPIRED — avoids the redirect side effect; this is
        // a data-plane-style 401 the caller must surface, not bounce on.
        return problem("Session invalid", 401, "ERR_AUTH_INVALID_TOKEN");
      }),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/session has expired/i);
    // fail closed: no partial preview from page 1 ever rendered.
    expect(screen.queryByText("bundle-xyz")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download bundle/i })).not.toBeInTheDocument();
  });

  it("test_cursor_invalid_relabels_generate_again_and_starts_fresh_call", async () => {
    const user = userEvent.setup();
    let call = 0;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, ({ request }) => {
        call += 1;
        const url = new URL(request.url);
        const hasToken = url.searchParams.has("bundle_token");
        if (!hasToken && call <= 2) {
          // first attempt's first page always succeeds
          return HttpResponse.json(
            makePage({
              sections: {
                audit_events: emptySection({ items: [{ id: "a1" }] as unknown as never[], has_more: true }),
                request_log_metadata: emptySection(),
                usage_lineage: emptySection(),
              },
              bundle_token: "tok-1",
            }),
          );
        }
        if (hasToken && call === 2) {
          return problem("Bundle continuation expired", 422, "ERR_CURSOR_INVALID");
        }
        return HttpResponse.json(makePage());
      }),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/interrupted/i);
    const retryBtn = await screen.findByRole("button", { name: /generate again/i });

    await user.click(retryBtn);
    // a fresh, un-tokened walk — resolves to the untokened success page.
    await waitFor(() => expect(screen.getByText("bundle-xyz")).toBeInTheDocument());
  });

  it("test_timeout_mid_walk_no_download_affordance", async () => {
    const user = userEvent.setup();
    let call = 0;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => {
        call += 1;
        if (call === 1) {
          return HttpResponse.json(
            makePage({
              sections: {
                audit_events: emptySection({ items: [{ id: "a1" }] as unknown as never[], has_more: true }),
                request_log_metadata: emptySection(),
                usage_lineage: emptySection(),
              },
              bundle_token: "tok-1",
            }),
          );
        }
        return problem("Upstream timed out", 504, "ERR_EXPORT_TIMEOUT");
      }),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/timed out/i);
    expect(screen.queryByRole("button", { name: /download bundle/i })).not.toBeInTheDocument();
    // the picker stays mounted (timeout is user-actionable — try a shorter period).
    expect(screen.getByLabelText(/^from$/i)).toBeInTheDocument();
  }, 15000);

  it("test_double_generate_click_is_noop_while_in_flight", async () => {
    const user = userEvent.setup();
    let calls = 0;
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, async () => {
        calls += 1;
        await new Promise((r) => setTimeout(r, 30));
        return HttpResponse.json(makePage());
      }),
    );
    render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await user.type(screen.getByLabelText(/^from$/i), "2026-06-01T00:00");
    await user.type(screen.getByLabelText(/^to$/i), "2026-06-30T00:00");

    const generateBtn = screen.getByRole("button", { name: /^generate$/i });
    await user.click(generateBtn);
    // The click handler updates walkState synchronously (before the awaited
    // fetch resolves), so by the time `await user.click(...)` returns, React
    // has already flushed the "generating" render — assert SYNCHRONOUSLY here
    // (not an async findByRole poll) so a slow/loaded test run can't let the
    // 30ms mock delay elapse before the in-flight state is ever observed.
    const inFlightBtn = screen.getByRole("button", { name: /generating/i });
    expect(inFlightBtn).toBeDisabled();

    // R8: even a raw synthetic click bypassing the disabled attribute is a no-op —
    // the internal `walkState === "generating"` guard in handleGenerate, not just
    // the disabled attribute, is what stops a second fetch.
    fireEvent.click(inFlightBtn);

    await waitFor(() => expect(screen.getByText("bundle-xyz")).toBeInTheDocument());
    expect(calls).toBe(1);
  });
});

// ── accessibility (M13): zero serious/critical axe violations across the states
// a real user hits — idle picker, error, and a populated preview. Loading/empty
// states for the two lower halves have their own dedicated coverage in
// schedule-control.test.tsx / generated-reports-list.test.tsx.
describe("ComplianceReportCenter — accessibility", () => {
  it("test_axe_idle_picker_state", async () => {
    server.use(...baseHandlers());
    const { container } = render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await screen.findByLabelText(/^from$/i);
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_error_state_field_validation", async () => {
    const user = userEvent.setup();
    server.use(...baseHandlers());
    const { container } = render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await user.type(screen.getByLabelText(/^from$/i), "2026-06-30T00:00");
    await user.type(screen.getByLabelText(/^to$/i), "2026-06-01T00:00");
    await user.click(screen.getByRole("button", { name: /^generate$/i }));
    await screen.findByText(/from must be on or before to/i);
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_forbidden_full_replacement_state", async () => {
    const user = userEvent.setup();
    server.use(
      ...baseHandlers(),
      http.get(BUNDLE_PATH, () => problem("Owner or admin required", 403, "ERR_AUTH_FORBIDDEN")),
    );
    const { container } = render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);
    await screen.findByRole("alert");
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });

  it("test_axe_populated_preview_state", async () => {
    const user = userEvent.setup();
    server.use(...baseHandlers(), http.get(BUNDLE_PATH, () => HttpResponse.json(makePage())));
    const { container } = render(<ComplianceReportCenter />, { wrapper: Wrapper });
    await fillPeriodAndGenerate(user);
    await screen.findByText("bundle-xyz");
    await expectNoSeriousViolations(container, { rules: { "color-contrast": { enabled: false } } });
  });
});
