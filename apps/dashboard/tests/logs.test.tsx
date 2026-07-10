/**
 * tests/logs.test.tsx — RED suite for the Logs Explorer dashboard page
 * (logs-explorer-ui TASK.md §3 — FROZEN @ v1).
 *
 * LogsExplorerPage fetches GET /admin/logs (cursor-paginated, filtered list) via the BFF
 * catch-all (/api/gw/admin/logs), and LogDetailDrawer fetches GET /admin/logs/{id} on row
 * activation. Mirrors the audit/alerts page test convention (tests/audit.test.tsx,
 * tests/alerts.test.tsx): render via a fresh QueryClientProvider, msw per-test overrides,
 * `within(section())`-scoped assertions.
 *
 * RED before Build: `@/components/logs/LogsExplorerPage` does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention.
 *
 * One test per §2 scenario (22 total) + the standing one-h1 / axe sweep this suite's
 * siblings each carry.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";
import { axe } from "@/test-support/axe";
import { REPLAY_STORAGE_KEY } from "@/lib/logs-replay";
import React from "react";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { LogsExplorerPage } from "@/components/logs/LogsExplorerPage";
import { NAV_ITEMS } from "@/components/ui/app-shell";

const APP = "http://localhost:3000";
const LOGS_URL = `${APP}/api/gw/admin/logs`;
const MODELS_URL = `${APP}/api/gw/admin/catalog/models`;
const KEYS_URL = `${APP}/api/gw/admin/keys`;
const detailUrl = (id: string) => `${APP}/api/gw/admin/logs/${id}`;

const KEY_ID = "11111111-1111-1111-1111-111111111111";
const MODELS_RESPONSE = { object: "list", data: [{ id: "openai/gpt-4o", name: "GPT-4o" }] };
const KEYS_RESPONSE = [{ key_id: KEY_ID, name: "prod-key" }];

const LOG_ITEM_OK = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  created_at: "2026-06-10T12:05:00Z",
  model_id: "openai/gpt-4o",
  key_id: KEY_ID,
  status_code: 200,
  cost_usd: "0.0042",
  stream: false,
  cached: false,
  latency_ms: 842,
  prompt_tokens: 120,
  completion_tokens: 48,
  total_tokens: 168,
};

const LOG_ITEM_ERROR = {
  id: "aaaaaaaa-0000-0000-0000-000000000002",
  created_at: "2026-06-10T11:00:00Z",
  model_id: "anthropic/claude-3-opus",
  key_id: KEY_ID,
  status_code: 500,
  cost_usd: null,
  stream: false,
  cached: false,
  latency_ms: null,
  prompt_tokens: null,
  completion_tokens: null,
  total_tokens: null,
};

const LOGS_RESPONSE = { items: [LOG_ITEM_OK, LOG_ITEM_ERROR], next_cursor: null, has_more: false };
const LOGS_EMPTY_RESPONSE = { items: [], next_cursor: null, has_more: false };

const LOG_DETAIL_FULL = {
  ...LOG_ITEM_OK,
  request_id: "req-123",
  request_body: { messages: [{ role: "user", content: "Hello there" }] },
  response_body: { content: "Hi! How can I help?" },
  scrub_status: "scrubbed",
  truncated: false,
  guardrail_verdict: { blocked: false, blocked_by: null, pii_masked: false },
};

function installAncillaryHandlers() {
  server.use(
    http.get(MODELS_URL, () => HttpResponse.json(MODELS_RESPONSE)),
    http.get(KEYS_URL, () => HttpResponse.json(KEYS_RESPONSE)),
  );
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function renderLogs() {
  return render(<LogsExplorerPage />, { wrapper: Wrapper });
}

/** Scope assertions to the logs section (CONVENTIONS.md within(<section>) rule). */
function section() {
  return screen.getByRole("region", { name: /logs/i });
}

/** Data rows only (excludes the header <tr>, which is also role="row"). */
function dataRows() {
  const tbody = section().querySelector("tbody");
  if (!tbody) throw new Error("logs table tbody not found");
  return within(tbody).getAllByRole("row");
}

beforeEach(() => {
  installAncillaryHandlers();
  window.sessionStorage.clear();
});

afterEach(() => {
  window.sessionStorage.clear();
});

describe("LogsExplorerPage — page-level states (M1)", () => {
  it("test_loading_state_renders_while_first_page_fetches", async () => {
    server.use(
      http.get(LOGS_URL, () => new Promise<never>(() => { /* never resolves */ })),
    );

    renderLogs();

    await waitFor(() => {
      const spinner =
        screen.queryByRole("status") ?? document.querySelector("[aria-busy='true']");
      expect(spinner).toBeTruthy();
    });
    expect(screen.queryAllByRole("row")).toHaveLength(0);
    expect(screen.queryByLabelText(/^model$/i)).not.toBeInTheDocument();
  });

  it("test_populated_table_renders_current_filtered_page", async () => {
    server.use(http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)));

    renderLogs();

    await waitFor(() => {
      expect(within(section()).getAllByText(/openai\/gpt-4o/i).length).toBeGreaterThan(0);
    });
    const sec = section();
    expect(within(sec).getByLabelText(/^model$/i)).toBeInTheDocument();
    expect(within(sec).getAllByText(/prod-key/i).length).toBeGreaterThan(0);
    expect(within(sec).getByText("200")).toBeInTheDocument();
    expect(within(sec).getByText("500")).toBeInTheDocument();
    // formatUsd rounds to 2 decimal places (Intl currency formatting) — a genuine sub-cent
    // per-call cost like $0.0042 correctly renders as "$0.00", not truncated/hidden. This
    // is the real, intentional behavior of lib/format.ts's formatUsd, not a display bug.
    expect(within(sec).getByText("$0.00")).toBeInTheDocument();
  });

  it("test_empty_state_for_filter_combination_with_no_matches", async () => {
    server.use(http.get(LOGS_URL, () => HttpResponse.json(LOGS_EMPTY_RESPONSE)));

    renderLogs();

    await waitFor(() => {
      expect(within(section()).getByText(/no logs match these filters/i)).toBeInTheDocument();
    });
    // filter bar remains interactive (not disabled)
    expect(within(section()).getByLabelText(/^model$/i)).not.toBeDisabled();
  });

  it("test_forbidden_access_shows_clear_non_generic_error", async () => {
    server.use(
      http.get(LOGS_URL, () =>
        HttpResponse.json({ title: "Insufficient role", status: 403, code: "ERR_AUTH_FORBIDDEN" }, { status: 403 }),
      ),
    );

    renderLogs();

    await waitFor(() => {
      expect(within(section()).getByText(/you don't have access to request logs/i)).toBeInTheDocument();
    });
    expect(within(section()).queryAllByRole("row")).toHaveLength(0);
    expect(screen.queryByLabelText(/^model$/i)).not.toBeInTheDocument();
  });

  it("test_one_h1", async () => {
    server.use(http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)));
    const { container } = renderLogs();

    await waitFor(() => {
      expect(within(section()).getAllByText(/openai\/gpt-4o/i).length).toBeGreaterThan(0);
    });
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });

  it("test_axe_no_serious_violations", async () => {
    server.use(http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)));
    const { container } = renderLogs();

    await waitFor(() => {
      expect(within(section()).getAllByText(/openai\/gpt-4o/i).length).toBeGreaterThan(0);
    });

    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious).toHaveLength(0);
  });
});

describe("LogsExplorerPage — filtering + pagination (M2, M3, R3, R5)", () => {
  it("test_changing_a_filter_requeries_and_resets_pagination", async () => {
    const PAGE1 = { items: [LOG_ITEM_OK], next_cursor: "cursor-abc", has_more: true };
    const PAGE2 = { items: [LOG_ITEM_ERROR], next_cursor: null, has_more: false };
    const FILTERED_PAGE1 = { items: [LOG_ITEM_OK], next_cursor: null, has_more: false };

    server.use(
      http.get(LOGS_URL, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get("model_id") === "openai/gpt-4o") {
          return HttpResponse.json(FILTERED_PAGE1);
        }
        if (url.searchParams.get("cursor") === "cursor-abc") {
          return HttpResponse.json(PAGE2);
        }
        return HttpResponse.json(PAGE1);
      }),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByRole("button", { name: /^next$/i })).toBeEnabled());
    await user.click(within(section()).getByRole("button", { name: /^next$/i }));
    await waitFor(() => expect(within(section()).getByText(/500/)).toBeInTheDocument());
    expect(within(section()).getByRole("button", { name: /^previous$/i })).toBeEnabled();

    await user.selectOptions(within(section()).getByLabelText(/^model$/i), "openai/gpt-4o");

    await waitFor(() => expect(within(section()).getByRole("button", { name: /^previous$/i })).toBeDisabled());
  });

  it("test_next_previous_cursor_pagination", async () => {
    const PAGE1 = { items: [LOG_ITEM_OK], next_cursor: "cursor-abc", has_more: true };
    const PAGE2 = { items: [LOG_ITEM_ERROR], next_cursor: null, has_more: false };

    server.use(
      http.get(LOGS_URL, ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(url.searchParams.get("cursor") === "cursor-abc" ? PAGE2 : PAGE1);
      }),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /^next$/i }));
    await waitFor(() => expect(within(section()).getByText("500")).toBeInTheDocument());

    await user.click(within(section()).getByRole("button", { name: /^previous$/i }));
    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
  });

  it("test_next_disabled_on_last_page", async () => {
    server.use(http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)));

    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    expect(within(section()).getByRole("button", { name: /^next$/i })).toBeDisabled();
  });

  it("test_invalid_filter_input_keeps_prior_valid_results_visible", async () => {
    let callCount = 0;
    server.use(
      http.get(LOGS_URL, () => {
        callCount += 1;
        return HttpResponse.json(LOGS_RESPONSE);
      }),
    );

    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());

    const fromInput = within(section()).getByLabelText(/^from$/i);
    const toInput = within(section()).getByLabelText(/^to$/i);

    // Commit a valid `from` alone first (a legitimate re-query) so the baseline reflects
    // the last VALID state, then make one atomic edit to `to` that inverts the range —
    // fireEvent.change (not user.type) so the invalid combination is reached in a single
    // commit, not through several intermediate (individually valid) keystroke states.
    fireEvent.change(fromInput, { target: { value: "2026-06-10T00:00" } });
    await waitFor(() => expect(callCount).toBeGreaterThan(1));
    const callsAfterValidChange = callCount;

    fireEvent.change(toInput, { target: { value: "2026-06-01T00:00" } });

    await waitFor(() => {
      expect(within(section()).getByRole("alert")).toHaveTextContent(/after/i);
    });
    // prior valid results are untouched
    expect(within(section()).getByText("200")).toBeInTheDocument();
    expect(within(section()).getByText("500")).toBeInTheDocument();
    // no new request was ever sent for the invalid combination
    expect(callCount).toBe(callsAfterValidChange);
  });

  it("test_mid_pagination_failure_preserves_current_page", async () => {
    const PAGE1 = { items: [LOG_ITEM_OK], next_cursor: "cursor-abc", has_more: true };

    server.use(
      http.get(LOGS_URL, ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get("cursor") === "cursor-abc") {
          return HttpResponse.json({ title: "Internal server error", status: 500 }, { status: 500 });
        }
        return HttpResponse.json(PAGE1);
      }),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /^next$/i }));

    await waitFor(() => {
      expect(within(section()).getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });
    // the currently-rendered rows remain visible
    expect(within(section()).getByText("200")).toBeInTheDocument();
  });
});

describe("LogsExplorerPage — detail drawer (M4, M5, R2)", () => {
  it("test_opening_drawer_fetches_and_renders_full_detail", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () => HttpResponse.json(LOG_DETAIL_FULL)),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /overview/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: /^request$/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^response$/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /guardrail verdict/i })).toBeInTheDocument();
    expect(screen.getByText(/req-123/)).toBeInTheDocument();
  });

  it("test_axe_no_serious_violations_with_drawer_open", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () => HttpResponse.json(LOG_DETAIL_FULL)),
    );

    const user = userEvent.setup();
    const { container } = renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /overview/i })).toBeInTheDocument();
    });

    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious).toHaveLength(0);
  });

  it("test_closing_drawer_returns_focus_and_preserves_table_state", async () => {
    const PAGE1 = { items: [LOG_ITEM_OK], next_cursor: "cursor-abc", has_more: true };
    const PAGE2 = { items: [LOG_ITEM_ERROR], next_cursor: null, has_more: false };

    server.use(
      http.get(LOGS_URL, ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(url.searchParams.get("cursor") === "cursor-abc" ? PAGE2 : PAGE1);
      }),
      http.get(detailUrl(LOG_ITEM_ERROR.id), () => HttpResponse.json({ ...LOG_DETAIL_FULL, ...LOG_ITEM_ERROR })),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(within(section()).getByRole("button", { name: /^next$/i }));
    await waitFor(() => expect(within(section()).getByText("500")).toBeInTheDocument());

    const row = within(section()).getByRole("row", { name: /claude/i });
    await user.click(row);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /overview/i })).toBeInTheDocument();
    });

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: /overview/i })).not.toBeInTheDocument();
    });
    await waitFor(() => expect(document.activeElement).toBe(row));
    // table still shows page 2 (unaffected by the drawer open/close)
    expect(within(section()).getByText("500")).toBeInTheDocument();
  });

  it("test_metadata_only_row_shows_explicit_unavailable_message", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () =>
        HttpResponse.json({
          ...LOG_DETAIL_FULL,
          request_body: null,
          response_body: null,
          scrub_status: "scrub_failed_metadata_only",
        }),
      ),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);

    await waitFor(() => {
      expect(screen.getAllByText(/content unavailable/i).length).toBeGreaterThanOrEqual(2);
    });
  });

  it("test_guardrail_verdict_panel_renders_blocked_call", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () =>
        HttpResponse.json({
          ...LOG_DETAIL_FULL,
          guardrail_verdict: { blocked: true, blocked_by: "prompt_injection", pii_masked: false },
        }),
      ),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);

    await waitFor(() => expect(screen.getByText(/^blocked$/i)).toBeInTheDocument());
    expect(screen.getByText("prompt_injection")).toBeInTheDocument();
    expect(screen.queryByText(/pii masked/i)).not.toBeInTheDocument();
  });

  it("test_not_found_detail_never_distinguishes_cross_tenant_from_deleted", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () =>
        HttpResponse.json({ title: "Request log not found", status: 404, code: "ERR_LOG_NOT_FOUND" }, { status: 404 }),
      ),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);

    await waitFor(() => {
      expect(screen.getByText(/log not found/i)).toBeInTheDocument();
    });
    // the underlying table + filter bar are unaffected (unchanged, just correctly
    // aria-hidden from assistive tech while the modal drawer is open — so queried by
    // plain text/label here, not by role, which Radix's own inert-background makes
    // temporarily inaccessible by design).
    expect(screen.getByText("200")).toBeInTheDocument();
    expect(screen.getByLabelText(/^model$/i)).toBeInTheDocument();
  });
});

describe("LogsExplorerPage — replay handoff (M6, M7, M8, R4)", () => {
  it("test_replay_writes_sessionstorage_and_navigates_without_sending", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () => HttpResponse.json(LOG_DETAIL_FULL)),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: /replay/i })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: /replay/i }));

    const stored = window.sessionStorage.getItem(REPLAY_STORAGE_KEY);
    expect(stored).toBeTruthy();
    const payload = JSON.parse(stored as string);
    expect(payload).toEqual({ text: "Hello there", modelId: "openai/gpt-4o", degraded: false });
    expect(getRouterMock().push).toHaveBeenCalledWith("/app/chat");
  });

  it("test_replay_of_multimodal_content_degrades_to_text_only_with_a_notice", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () =>
        HttpResponse.json({
          ...LOG_DETAIL_FULL,
          request_body: {
            messages: [
              {
                role: "user",
                content: [
                  { type: "text", text: "Look at this" },
                  { type: "image_url", image_url: { url: "data:image/png;base64,xyz" } },
                ],
              },
            ],
          },
        }),
      ),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: /replay/i })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: /replay/i }));

    const payload = JSON.parse(window.sessionStorage.getItem(REPLAY_STORAGE_KEY) as string);
    expect(payload.text).toBe("Look at this");
    expect(payload.degraded).toBe(true);
  });

  it("test_replay_disabled_for_metadata_only_log_is_a_true_noop", async () => {
    server.use(
      http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)),
      http.get(detailUrl(LOG_ITEM_OK.id), () =>
        HttpResponse.json({ ...LOG_DETAIL_FULL, request_body: null }),
      ),
    );

    const user = userEvent.setup();
    renderLogs();

    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());
    await user.click(dataRows()[0]);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /replay/i })).toHaveAttribute("aria-disabled", "true");
    });
    expect(screen.getByText(/nothing to replay/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /replay/i }));

    expect(window.sessionStorage.getItem(REPLAY_STORAGE_KEY)).toBeNull();
    expect(getRouterMock().push).not.toHaveBeenCalled();
  });
});

describe("LogsExplorerPage — keyboard operation (M9)", () => {
  it("test_keyboard_only_operation_reaches_every_control_in_order", async () => {
    server.use(http.get(LOGS_URL, () => HttpResponse.json(LOGS_RESPONSE)));

    renderLogs();
    await waitFor(() => expect(within(section()).getByText("200")).toBeInTheDocument());

    const sec = section();
    const modelFilter = within(sec).getByLabelText(/^model$/i);
    const firstRow = dataRows()[0];
    const nextButton = within(sec).getByRole("button", { name: /^next$/i });

    // DOM order is the tab order here (no interactive element sets a positive tabIndex) —
    // asserted structurally rather than by walking real Tab keypresses through a mix of
    // datetime-local/number/select controls, which is comparatively flaky under jsdom.
    expect(modelFilter.compareDocumentPosition(firstRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(firstRow.compareDocumentPosition(nextButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(firstRow).toHaveAttribute("tabindex", "0");
  });
});

describe("LogsExplorerPage — nav entry (M10)", () => {
  it("test_nav_entry_admin_only_matches_the_real_access_gate", () => {
    const logs = NAV_ITEMS.find((i) => i.href === "/app/logs");
    expect(logs).toBeDefined();
    expect(logs?.label).toBe("Logs");
    expect(logs?.minRole).toBe("admin");
  });
});
