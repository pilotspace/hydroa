"use client";

/**
 * LogsExplorerPage — the console Logs Explorer (logs-explorer-ui TASK.md §3 CONTRACT).
 * Orchestrator: owns LogsFilters state + a client cursor stack, wires the list query to
 * GET /admin/logs (via the BFF catch-all), renders the page-level four states (M1) around
 * it, and hosts LogsFilterBar + LogsTable + LogDetailDrawer.
 *
 * Consumes GET /admin/logs (list) + GET /admin/logs/{id} (detail, inside LogDetailDrawer) —
 * the FROZEN @ v1 logs-explorer-api. LOGS_READ gated server-side (owner/admin/operator/
 * superadmin; other roles -> 403); the "Logs" nav entry (minRole:"admin" in app-shell.tsx)
 * is UX-only — this page's own R1 handling is the defense-in-depth for a direct URL visit
 * with an insufficient role (M10).
 */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { writeReplayPayload, type ReplayPayload } from "@/lib/logs-replay";
import { LogsFilterBar, type LogsFilters } from "./LogsFilterBar";
import { LogsTable, type LogListItem } from "./LogsTable";
import { LogDetailDrawer, type LogDetail } from "./LogDetailDrawer";

interface LogsPage {
  items: LogListItem[];
  next_cursor: string | null;
  has_more: boolean;
}

interface ModelEntry {
  id: string;
  name?: string;
}
interface ModelsData {
  object: string;
  data: ModelEntry[];
}

interface ApiKeyBrief {
  key_id: string;
  name: string;
}

const DEFAULT_FILTERS: LogsFilters = { status: "all" };

/** Client-side pre-flight validation (R3) — never sends a combination the API would
 *  reject, so the previously-valid result set is never disturbed by an invalid draft. */
function validateFilters(f: LogsFilters): Partial<Record<keyof LogsFilters, string>> {
  const errors: Partial<Record<keyof LogsFilters, string>> = {};

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
  if (
    !errors.costMin &&
    !errors.costMax &&
    f.costMin !== undefined &&
    f.costMin !== "" &&
    f.costMax !== undefined &&
    f.costMax !== "" &&
    Number(f.costMin) > Number(f.costMax)
  ) {
    errors.costMax = "Max must be ≥ Min";
  }
  return errors;
}

function buildLogsQuery(filters: LogsFilters, cursor: string | undefined): string {
  const params = new URLSearchParams();
  if (filters.from) {
    const ms = Date.parse(filters.from);
    if (!Number.isNaN(ms)) params.set("since", new Date(ms).toISOString());
  }
  if (filters.to) {
    const ms = Date.parse(filters.to);
    if (!Number.isNaN(ms)) params.set("until", new Date(ms).toISOString());
  }
  if (filters.modelId) params.set("model_id", filters.modelId);
  if (filters.keyId) params.set("key_id", filters.keyId);
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.costMin) params.set("cost_min", filters.costMin);
  if (filters.costMax) params.set("cost_max", filters.costMax);
  if (cursor) params.set("cursor", cursor);
  const qs = params.toString();
  return qs ? `/admin/logs?${qs}` : "/admin/logs";
}

/** Flatten a captured request's user-turn text content; degraded=true when any non-text
 *  (e.g. image) message part was present and dropped (M8). */
function extractReplayText(requestBody: LogDetail["request_body"]): { text: string; degraded: boolean } {
  if (!requestBody) return { text: "", degraded: false };
  let degraded = false;
  const parts: string[] = [];
  for (const message of requestBody.messages ?? []) {
    if (message.role !== "user") continue;
    const content = message.content;
    if (typeof content === "string") {
      parts.push(content);
    } else if (Array.isArray(content)) {
      for (const part of content) {
        if (part && typeof part === "object" && "type" in part) {
          const typed = part as { type?: unknown; text?: unknown };
          if (typed.type === "text" && typeof typed.text === "string") {
            parts.push(typed.text);
            continue;
          }
        }
        degraded = true;
      }
    } else if (content != null) {
      degraded = true;
    }
  }
  return { text: parts.join("\n\n"), degraded };
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError && err.status === 403) return "You don't have access to Request Logs";
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load logs";
}

export function LogsExplorerPage() {
  const router = useRouter();

  // `filters` is the DRAFT bound to the filter bar's controls (reflects every keystroke,
  // including a momentarily-invalid combination, so typing is never blocked). `committedFilters`
  // is the last VALID draft and is the only thing the query ever reads — an invalid draft
  // therefore never fires a request, satisfying R3 by construction (pre-flight prevention,
  // not a round trip to observe the API's own ERR_PAYLOAD_INVALID).
  const [filters, setFilters] = useState<LogsFilters>(DEFAULT_FILTERS);
  const [committedFilters, setCommittedFilters] = useState<LogsFilters>(DEFAULT_FILTERS);
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const [openLogId, setOpenLogId] = useState<string | null>(null);

  const fieldErrors = useMemo(() => validateFilters(filters), [filters]);
  const currentCursor = cursorStack[cursorStack.length - 1];

  const logsQuery = useQuery<LogsPage>({
    queryKey: ["admin-logs", committedFilters, currentCursor],
    queryFn: () => bffGet<LogsPage>(buildLogsQuery(committedFilters, currentCursor)),
    placeholderData: keepPreviousData,
    retry: false,
  });

  // R5: a page-N fetch failure (Next/Previous, or a filter re-query) must NOT drop the
  // already-rendered rows — tracked independently of TanStack's own `data`, which resets
  // to undefined once a query settles into its `error` state (`placeholderData: keepPreviousData`
  // above only covers the `pending`/fetching phase, not a settled error). `lastGoodPage` is
  // the last page this query EVER resolved successfully, regardless of the current query's
  // live status. React's documented "adjust state during render" escape hatch (same idiom as
  // SpendPage's lastGood tracking) — NOT a useEffect, so react-hooks/set-state-in-effect stays
  // clean: this is pure derived state, self-terminating once lastGoodPage === logsQuery.data.
  const [lastGoodPage, setLastGoodPage] = useState<LogsPage | null>(null);
  if (logsQuery.data !== undefined && logsQuery.data !== lastGoodPage) {
    setLastGoodPage(logsQuery.data);
  }

  const modelsQuery = useQuery<ModelsData>({
    queryKey: ["admin-catalog-models"],
    queryFn: () => bffGet<ModelsData>("/admin/catalog/models"),
    retry: false,
  });

  const keysQuery = useQuery<ApiKeyBrief[]>({
    queryKey: ["admin-keys"],
    queryFn: () => bffGet<ApiKeyBrief[]>("/admin/keys"),
    retry: false,
  });

  const models = useMemo(
    () => (modelsQuery.data?.data ?? []).map((m) => ({ id: m.id, label: m.name ?? m.id })),
    [modelsQuery.data],
  );
  // fail-open (ModelPicker's own convention): a fetch error just yields no filter options,
  // never blocks the page.
  const keys = useMemo(
    () => (keysQuery.data ?? []).map((k) => ({ id: k.key_id, label: k.name ?? k.key_id })),
    [keysQuery.data],
  );
  const keysById = useMemo(() => {
    const map: Record<string, string> = {};
    for (const k of keysQuery.data ?? []) map[k.key_id] = k.name ?? k.key_id;
    return map;
  }, [keysQuery.data]);

  function handleFiltersChange(next: LogsFilters) {
    setFilters(next);
    if (Object.keys(validateFilters(next)).length === 0) {
      // M2: a valid change re-queries and resets pagination to the first page.
      setCommittedFilters(next);
      setCursorStack([undefined]);
    }
    // else: R3 — committedFilters (and therefore the query + the on-screen rows) stays
    // untouched; the invalid draft is still reflected in the controls + fieldErrors.
  }

  function handleRowActivate(id: string) {
    setOpenLogId(id);
  }

  // Focus-return-to-triggering-row is Radix Dialog's own built-in behavior (records
  // document.activeElement when the drawer's FocusScope activates, restores it on close) —
  // not reimplemented here. LogsTable focuses the row synchronously on activation, before
  // the drawer mounts, so Radix captures the right element.
  function handleDrawerClose() {
    setOpenLogId(null);
  }

  function handleReplay(detail: LogDetail) {
    if (!detail.request_body) return; // R4 defense-in-depth mirror of the drawer's own guard
    const { text, degraded } = extractReplayText(detail.request_body);
    const payload: ReplayPayload = { text, modelId: detail.model_id, degraded };
    writeReplayPayload(payload);
    router.push("/app/chat");
  }

  const hasAnyData = lastGoodPage !== null;
  const isInitialLoading = logsQuery.isLoading && !hasAnyData;
  const isInitialError = logsQuery.isError && !hasAnyData;

  return (
    <section aria-labelledby="logs-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Logs"
        titleId="logs-heading"
        description="Browse, filter, and inspect this tenant's request-level call history."
      />

      {isInitialLoading ? (
        <Loading label="Loading logs…" />
      ) : isInitialError ? (
        <ErrorState title={getErrorTitle(logsQuery.error)} />
      ) : (
        <>
          <LogsFilterBar
            value={filters}
            onChange={handleFiltersChange}
            models={models}
            keys={keys}
            fieldErrors={fieldErrors}
            disabled={logsQuery.isFetching}
          />

          <LogsTable
            items={lastGoodPage?.items ?? []}
            keysById={keysById}
            onRowActivate={handleRowActivate}
            hasMore={lastGoodPage?.has_more ?? false}
            canGoPrevious={cursorStack.length > 1}
            onNext={() => {
              const next = lastGoodPage?.next_cursor;
              if (next) setCursorStack((stack) => [...stack, next]);
            }}
            onPrevious={() => setCursorStack((stack) => (stack.length > 1 ? stack.slice(0, -1) : stack))}
          />

          {/* R5: a page-N fetch failure keeps the currently-rendered rows (lastGoodPage)
              visible, with an inline retry beneath the pager rather than a page-level error. */}
          {logsQuery.isError && hasAnyData ? (
            <ErrorState title={getErrorTitle(logsQuery.error)} onRetry={() => logsQuery.refetch()} />
          ) : null}
        </>
      )}

      <LogDetailDrawer logId={openLogId} onClose={handleDrawerClose} onReplay={handleReplay} />
    </section>
  );
}
