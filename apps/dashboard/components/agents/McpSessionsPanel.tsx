"use client";

/**
 * McpSessionsPanel — agents-console TASK.md §3 CONTRACT M6-M8. Wires
 * McpSessionsFilterBar + the REUSED LogsTable/LogDetailDrawer (verbatim, same props,
 * same GET /admin/logs / GET /admin/logs/{id}) into the Sessions tab.
 *
 * Ground Issue 3 / M6/M7: GET /admin/logs's model_id filter is exact-match only — there
 * is no server-side way to ask for "every MCP row" across pages. When the exact
 * server::tool field is EMPTY, the fetched page is ADDITIONALLY narrowed client-side to
 * `model_id.startsWith("mcp::")` before rendering, and a visible banner discloses that
 * this is a page-scoped approximation (a tracked change request, not a silent gap) —
 * never presented as a complete result set. When the field IS set, the underlying query
 * itself carries the exact model_id and the banner is hidden (the API result is already
 * precise for that one shape).
 *
 * M8: LogDetailDrawer is reused UNCHANGED (its own frozen 404/guardrail-verdict
 * handling is untouched) — the "trace cost ≠ billed cost" disclosure this task adds is
 * therefore rendered as a SIBLING note here, not inside the drawer itself.
 */

import { useMemo, useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { LogsTable, type LogListItem } from "@/components/logs/LogsTable";
import { LogDetailDrawer, type LogDetail } from "@/components/logs/LogDetailDrawer";
import { McpSessionsFilterBar, type McpSessionsFilters } from "./McpSessionsFilterBar";

interface LogsPage {
  items: LogListItem[];
  next_cursor: string | null;
  has_more: boolean;
}

const DEFAULT_FILTERS: McpSessionsFilters = { status: "all" };

function validateFilters(f: McpSessionsFilters): Partial<Record<keyof McpSessionsFilters, string>> {
  const errors: Partial<Record<keyof McpSessionsFilters, string>> = {};
  if (f.from && f.to) {
    const fromMs = Date.parse(f.from);
    const toMs = Date.parse(f.to);
    if (!Number.isNaN(fromMs) && !Number.isNaN(toMs) && fromMs > toMs) {
      errors.to = "To must be after From";
    }
  }
  if (f.costMin !== undefined && f.costMin !== "" && Number.isNaN(Number(f.costMin))) {
    errors.costMin = "Enter a valid amount";
  }
  if (f.costMax !== undefined && f.costMax !== "" && Number.isNaN(Number(f.costMax))) {
    errors.costMax = "Enter a valid amount";
  }
  return errors;
}

function buildQuery(filters: McpSessionsFilters, cursor: string | undefined): string {
  const params = new URLSearchParams();
  if (filters.from) {
    const ms = Date.parse(filters.from);
    if (!Number.isNaN(ms)) params.set("since", new Date(ms).toISOString());
  }
  if (filters.to) {
    const ms = Date.parse(filters.to);
    if (!Number.isNaN(ms)) params.set("until", new Date(ms).toISOString());
  }
  if (filters.serverTool) params.set("model_id", filters.serverTool);
  if (filters.keyId) params.set("key_id", filters.keyId);
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.costMin) params.set("cost_min", filters.costMin);
  if (filters.costMax) params.set("cost_max", filters.costMax);
  if (cursor) params.set("cursor", cursor);
  const qs = params.toString();
  return qs ? `/admin/logs?${qs}` : "/admin/logs";
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load sessions";
}

export interface McpSessionsPanelProps {
  keys: { id: string; label: string }[];
  keysById: Record<string, string>;
}

export function McpSessionsPanel({ keys, keysById }: McpSessionsPanelProps) {
  const [filters, setFilters] = useState<McpSessionsFilters>(DEFAULT_FILTERS);
  const [committedFilters, setCommittedFilters] = useState<McpSessionsFilters>(DEFAULT_FILTERS);
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const [openLogId, setOpenLogId] = useState<string | null>(null);

  const fieldErrors = useMemo(() => validateFilters(filters), [filters]);
  const currentCursor = cursorStack[cursorStack.length - 1];

  const logsQuery = useQuery<LogsPage>({
    queryKey: ["mcp-sessions-logs", committedFilters, currentCursor],
    queryFn: () => bffGet<LogsPage>(buildQuery(committedFilters, currentCursor)),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const [lastGoodPage, setLastGoodPage] = useState<LogsPage | null>(null);
  if (logsQuery.data !== undefined && logsQuery.data !== lastGoodPage) {
    setLastGoodPage(logsQuery.data);
  }

  function handleFiltersChange(next: McpSessionsFilters) {
    setFilters(next);
    if (Object.keys(validateFilters(next)).length === 0) {
      setCommittedFilters(next);
      setCursorStack([undefined]);
    }
  }

  const pageScoped = !committedFilters.serverTool;
  const items = lastGoodPage?.items ?? [];
  const visibleItems = pageScoped ? items.filter((i) => i.model_id.startsWith("mcp::")) : items;

  const hasAnyData = lastGoodPage !== null;
  const isInitialLoading = logsQuery.isLoading && !hasAnyData;
  const isInitialError = logsQuery.isError && !hasAnyData;

  return (
    <div className="flex flex-col gap-4">
      {pageScoped ? (
        <div
          role="status"
          className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning-foreground"
        >
          Showing MCP sessions found on the current page only — a broader, server-side filter is
          tracked as a change request, not shipped yet.
        </div>
      ) : null}

      {isInitialLoading ? (
        <Loading label="Loading sessions…" />
      ) : isInitialError ? (
        <ErrorState title={getErrorTitle(logsQuery.error)} />
      ) : (
        <>
          <McpSessionsFilterBar
            value={filters}
            onChange={handleFiltersChange}
            keys={keys}
            fieldErrors={fieldErrors}
            disabled={logsQuery.isFetching}
          />

          <LogsTable
            items={visibleItems}
            keysById={keysById}
            onRowActivate={setOpenLogId}
            hasMore={lastGoodPage?.has_more ?? false}
            canGoPrevious={cursorStack.length > 1}
            onNext={() => {
              const next = lastGoodPage?.next_cursor;
              if (next) setCursorStack((stack) => [...stack, next]);
            }}
            onPrevious={() => setCursorStack((stack) => (stack.length > 1 ? stack.slice(0, -1) : stack))}
          />

          {logsQuery.isError && hasAnyData ? (
            <ErrorState title={getErrorTitle(logsQuery.error)} onRetry={() => logsQuery.refetch()} />
          ) : null}
        </>
      )}

      {openLogId !== null ? (
        <p className="text-xs text-muted-foreground">
          The cost shown above is trace metadata only — the billed amount for this tool call is a
          separate invoice line, grouped by server and tool, visible on the tenant&apos;s Invoices page.
        </p>
      ) : null}

      <LogDetailDrawer
        logId={openLogId}
        onClose={() => setOpenLogId(null)}
        onReplay={() => {
          /* Replay-in-Chat is a chat-completion affordance the reused drawer always
             renders when request_body is captured; it carries no meaning for an MCP
             tool-call trace, so this is an intentional no-op rather than a fork of the
             frozen LogDetailDrawer component. */
        }}
      />
    </div>
  );
}

export type { LogDetail };
