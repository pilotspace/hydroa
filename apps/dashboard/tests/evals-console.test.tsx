/**
 * tests/evals-console.test.tsx — RED suite for the evals console (evals-console
 * TASK.md §3 — FROZEN @ v1, R7). Mirrors tests/agents-console.test.tsx's own
 * convention: fresh QueryClientProvider per render, msw per-test overrides via
 * server.use, within(section())-scoped assertions, MODULE_NOT_FOUND as the
 * established true-red signal.
 *
 * Exact `it(...)` titles are FROZEN — the ADD gate binds junit test ids to these
 * strings (see the task's <tests> block); never rename.
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import React from "react";

// ── These imports will cause MODULE_NOT_FOUND until the components exist — the
//    established true-red signal (see tests/agents-console.test.tsx's own header). ──
import { EvalsListPage } from "@/components/evals/EvalsListPage";
import { SetDetailPage } from "@/components/evals/SetDetailPage";
import { RunVerdictPage } from "@/components/evals/RunVerdictPage";

const APP = "http://localhost:3000";
const AUTH_ME_URL = `${APP}/api/auth/me`;
const SETS_URL = `${APP}/api/gw/admin/evals/sets`;
const setDetailUrl = (id: string) => `${APP}/api/gw/admin/evals/sets/${id}`;
const verdictUrl = (id: string) => `${APP}/api/gw/admin/evals/runs/${id}/verdict`;
const casesUrl = (id: string) => `${APP}/api/gw/admin/evals/runs/${id}/cases`;

const SET_ID = "es_a1b2c3d4";
const RUN_ID = "er_e5f6a7b8";
const BASELINE_RUN_ID = "er_00000000";
const CASE_PASS_ID = "ec_cccc1111";
const CASE_FAIL_ID = "ec_cccc2222";
const CASE_REFUSED_ID = "ec_cccc3333";
const CASE_ERRORED_ID = "ec_cccc4444";
const CASE_PENDING_ID = "ec_cccc5555";

function meResponse(role: string = "member") {
  return {
    user_id: "00000000-0000-0000-0000-000000000001",
    tenant_id: "00000000-0000-0000-0000-000000000099",
    email: "ada@acme.io",
    role,
    exp: Math.floor(Date.now() / 1000) + 86400,
  };
}

function installBaseHandlers(role: string = "member") {
  server.use(http.get(AUTH_ME_URL, () => HttpResponse.json(meResponse(role))));
}

const CASE_RESULT_PASS = {
  eval_case_id: CASE_PASS_ID,
  assertion: { kind: "exact_match", expected: "Sure thing!" },
  status: "completed",
  response_text: "Sure thing!",
  passed: true, // authoritative — from the backend scorer, never re-derived client-side
};

const CASE_RESULT_FAIL = {
  eval_case_id: CASE_FAIL_ID,
  assertion: { kind: "exact_match", expected: "Absolutely!" },
  status: "completed",
  response_text: "Nope, can't do that.",
  reason: "Assertion exact_match failed: response did not match expected value",
  passed: false,
};

// A `contains` case: expected "echo" is NOT string-equal to the actual "echo:one", yet the
// backend scorer PASSED it. The row must show the backend's `passed`, never re-derive fail from
// expected!==actual — this fixture guards against a regression back to client-side scoring.
const CASE_RESULT_CONTAINS_PASS = {
  eval_case_id: "ec_cccc6666",
  assertion: { kind: "contains", expected: "echo" },
  status: "completed",
  response_text: "echo:one",
  passed: true,
};

const CASE_RESULT_REFUSED = {
  eval_case_id: CASE_REFUSED_ID,
  assertion: { kind: "exact_match", expected: "Some answer" },
  status: "refused",
  reason: "Content policy violation",
};

const CASE_RESULT_ERRORED = {
  eval_case_id: CASE_ERRORED_ID,
  assertion: { kind: "exact_match", expected: "Some answer" },
  status: "errored",
  reason: "Upstream timeout",
};

const CASE_RESULT_PENDING = {
  eval_case_id: CASE_PENDING_ID,
  assertion: { kind: "exact_match", expected: "Some answer" },
  status: "pending",
};

function verdict(overrides: Partial<{
  score: { passed: number; total: number };
  baseline: { run_id: string; score: { passed: number; total: number } } | null;
  verdict: "pass" | "fail" | "no_baseline";
}> = {}) {
  return {
    object: "eval.verdict",
    run_id: RUN_ID,
    score: overrides.score ?? { passed: 2, total: 2 },
    baseline:
      overrides.baseline !== undefined
        ? overrides.baseline
        : { run_id: BASELINE_RUN_ID, score: { passed: 2, total: 2 } },
    verdict: overrides.verdict ?? "pass",
  };
}

function casesResponse(data: unknown[]) {
  return { object: "list", data };
}

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}
function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}
function renderRunVerdict(setId: string = SET_ID, runId: string = RUN_ID) {
  return render(<RunVerdictPage setId={setId} runId={runId} />, { wrapper: Wrapper });
}
function renderSetDetail(setId: string = SET_ID) {
  return render(<SetDetailPage setId={setId} />, { wrapper: Wrapper });
}
function renderEvalsList() {
  return render(<EvalsListPage />, { wrapper: Wrapper });
}
function section() {
  return screen.getByRole("region");
}

describe("RunVerdictPage — verdict leads the page (M3, R:VERDICT_NOT_PRIMARY)", () => {
  it("test_run_page_leads_with_verdict_banner", async () => {
    installBaseHandlers();
    server.use(
      http.get(verdictUrl(RUN_ID), () => HttpResponse.json(verdict({ verdict: "pass" }))),
      http.get(casesUrl(RUN_ID), () => HttpResponse.json(casesResponse([CASE_RESULT_PASS]))),
    );

    renderRunVerdict();

    const banner = await screen.findByTestId("verdict-banner");
    const list = await screen.findByTestId("case-diff-list");
    // DOM order: the banner precedes the case list — DOCUMENT_POSITION_FOLLOWING means
    // `list` comes AFTER `banner` in the tree.
    expect(banner.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(banner).getByText(/^pass$/i)).toBeInTheDocument();
  });
});

describe("CaseDiffRow — expected/actual/reason, never a fabricated actual (M4)", () => {
  it("test_case_diff_shows_expected_actual_reason", async () => {
    installBaseHandlers();
    server.use(
      http.get(verdictUrl(RUN_ID), () => HttpResponse.json(verdict({ score: { passed: 1, total: 2 }, verdict: "fail" }))),
      http.get(casesUrl(RUN_ID), () =>
        HttpResponse.json(
          casesResponse([CASE_RESULT_FAIL, CASE_RESULT_CONTAINS_PASS, CASE_RESULT_REFUSED]),
        ),
      ),
    );

    renderRunVerdict();

    const failRow = await screen.findByTestId(`case-diff-${CASE_FAIL_ID}`);
    expect(within(failRow).getByText("exact_match")).toBeInTheDocument();
    expect(within(failRow).getByText("Absolutely!")).toBeInTheDocument();
    expect(within(failRow).getByText("Nope, can't do that.")).toBeInTheDocument();
    expect(within(failRow).getByText(/assertion exact_match failed/i)).toBeInTheDocument();
    expect(within(failRow).getByText(/^fail$/i)).toBeInTheDocument();

    // The `contains` case is a PASS by the backend scorer even though expected !== actual —
    // the row shows the authoritative `passed`, never a client-side string comparison.
    const containsRow = screen.getByTestId("case-diff-ec_cccc6666");
    expect(within(containsRow).getByText("echo")).toBeInTheDocument();
    expect(within(containsRow).getByText("echo:one")).toBeInTheDocument();
    expect(within(containsRow).getByText(/^pass$/i)).toBeInTheDocument();
    expect(within(containsRow).queryByText(/^fail$/i)).not.toBeInTheDocument();

    const refusedRow = screen.getByTestId(`case-diff-${CASE_REFUSED_ID}`);
    expect(within(refusedRow).getByText(/^refused$/i)).toBeInTheDocument();
    expect(within(refusedRow).getByText(/content policy violation/i)).toBeInTheDocument();
    // no fabricated actual: neither the OTHER row's response text nor any concrete
    // "actual" value ever appears inside the refused row.
    expect(within(refusedRow).queryByText("Nope, can't do that.")).not.toBeInTheDocument();
    expect(within(refusedRow).queryByText("Sure thing!")).not.toBeInTheDocument();
    expect(within(refusedRow).getByText(/no response/i)).toBeInTheDocument();
  });
});

describe("VerdictBanner — no_baseline is an explicit state, never pass-styled (EDGES)", () => {
  it("test_no_baseline_run_renders_explicit_state", async () => {
    installBaseHandlers();
    server.use(
      http.get(verdictUrl(RUN_ID), () => HttpResponse.json(verdict({ baseline: null, verdict: "no_baseline" }))),
      http.get(casesUrl(RUN_ID), () => HttpResponse.json(casesResponse([CASE_RESULT_PASS]))),
    );

    renderRunVerdict();

    const banner = await screen.findByTestId("verdict-banner");
    expect(within(banner).getByText(/no baseline/i)).toBeInTheDocument();
    expect(within(banner).queryByText(/^pass$/i)).not.toBeInTheDocument();
    expect(within(banner).queryByText(/^fail$/i)).not.toBeInTheDocument();
  });
});

describe("VerdictBanner — a pending case forces a pending state (EDGES)", () => {
  it("test_pending_run_is_not_a_verdict", async () => {
    installBaseHandlers();
    server.use(
      // the verdict endpoint may still compute a (partial) pass — the banner must
      // NOT trust it while any case is still pending.
      http.get(verdictUrl(RUN_ID), () => HttpResponse.json(verdict({ verdict: "pass" }))),
      http.get(casesUrl(RUN_ID), () => HttpResponse.json(casesResponse([CASE_RESULT_PASS, CASE_RESULT_PENDING]))),
    );

    renderRunVerdict();

    const banner = await screen.findByTestId("verdict-banner");
    expect(within(banner).getByText(/pending/i)).toBeInTheDocument();
    expect(within(banner).queryByText(/^pass$/i)).not.toBeInTheDocument();
    expect(within(banner).queryByText(/^fail$/i)).not.toBeInTheDocument();
  });
});

describe("RunVerdictPage — fail-closed identity gate (M6, R:NULL_RENDER_LEAK)", () => {
  it("test_section_fail_closed_for_loading_identity", async () => {
    // /api/auth/me never resolves during this test — identity stays "loading" forever.
    server.use(http.get(AUTH_ME_URL, () => new Promise(() => {})));

    renderRunVerdict();

    // NEVER a null-leak: a visible Loading indicator is always on screen.
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    // Nothing actionable from the gated surface mounts while identity is unresolved.
    expect(screen.queryByTestId("verdict-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("case-diff-list")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /run verdict/i })).not.toBeInTheDocument();
  });
});

describe("Evals pages — error states name the subsystem, never the user's data (M6)", () => {
  it("test_error_state_names_subsystem_not_user_data", async () => {
    installBaseHandlers("member");
    server.use(
      http.get(verdictUrl(RUN_ID), () =>
        HttpResponse.json({ title: "Insufficient role", status: 403, code: "ERR_AUTH_FORBIDDEN" }, { status: 403 }),
      ),
      http.get(casesUrl(RUN_ID), () => HttpResponse.json(casesResponse([]))),
    );

    renderRunVerdict();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/you don't have access to evals/i);
    });
    // never blames the user's own data
    expect(screen.queryByText(/your data/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});

describe("SetDetailPage — run rows are keyboard navigable (M5)", () => {
  it("test_run_rows_keyboard_navigable", async () => {
    installBaseHandlers();
    const SET_DETAIL = {
      id: SET_ID,
      object: "eval.set",
      created_at: 1750000000,
      name: "Support Tone Regression",
      description: "Checks tone drift on refusal edge cases",
      cases: [],
      runs: [
        {
          id: RUN_ID,
          object: "eval.run",
          created_at: 1750000200,
          eval_set_id: SET_ID,
          model: "openai/gpt-4o",
          status: "completed",
          case_count: 2,
        },
      ],
      baseline_run_id: null,
    };
    server.use(http.get(setDetailUrl(SET_ID), () => HttpResponse.json(SET_DETAIL)));
    getRouterMock().push.mockClear();

    const user = userEvent.setup();
    renderSetDetail();

    await waitFor(() => expect(within(section()).getByText(/openai\/gpt-4o/)).toBeInTheDocument());
    const row = within(section()).getByRole("row", { name: /openai\/gpt-4o.*completed/i });

    expect(row).toHaveAttribute("tabindex", "0");
    expect(row.className).toMatch(/focus-visible:ring/);

    row.focus();
    await user.keyboard("{Enter}");
    expect(getRouterMock().push).toHaveBeenCalledWith(`/app/evals/${SET_ID}/runs/${RUN_ID}`);

    getRouterMock().push.mockClear();
    await user.keyboard(" ");
    // space is only meaningful while the row itself is focused — re-focus explicitly
    row.focus();
    await user.keyboard(" ");
    expect(getRouterMock().push).toHaveBeenCalledWith(`/app/evals/${SET_ID}/runs/${RUN_ID}`);
  });
});

describe("EvalsListPage — empty state points to the /v1 API (EDGES)", () => {
  it("test_empty_sets_points_to_api", async () => {
    installBaseHandlers();
    server.use(http.get(SETS_URL, () => HttpResponse.json({ object: "list", data: [] })));

    renderEvalsList();

    await waitFor(() => expect(within(section()).getByText(/no eval sets yet/i)).toBeInTheDocument());
    // Read-focused console: no in-console create — the empty state directs the operator to the
    // /v1 API to author sets, and there is NO create button anywhere in the section.
    expect(within(section()).getByText(/\/v1\/evals\/sets/i)).toBeInTheDocument();
    expect(within(section()).queryByRole("button", { name: /new eval set/i })).not.toBeInTheDocument();
  });
});
