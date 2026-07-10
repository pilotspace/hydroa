"use client";

/**
 * LogsTable — bespoke cursor-paginated request-log list, built DIRECTLY over the
 * ui/table.tsx primitives (Table/TableHeader/TableBody/TableRow/TableHead/TableCell +
 * the shared Empty state) — deliberately NOT a DataTable wrapper (logs-explorer-ui
 * TASK.md §3 CONTRACT, §0 Ground Issue #1): DataTable's `pageSizeOptions` pager is
 * client-side/page-index over an already-fetched in-memory array, which cannot fit a
 * cursor/keyset server API (logs-explorer-api mirrors audit/api/router.py:export_audit
 * — opaque cursor, no offset, no total) without eagerly fetching every page.
 *
 * Columns: Model, Key, Status, Latency, Tokens, Cost, When — the freeze-reconciled set
 * (latency_ms + prompt/completion/total tokens joined in once logs-explorer-api froze
 * on the request-log-metering-fields columns). Each row activates on click or
 * Enter/Space while focused (M4) — a native `<tr tabIndex=0>` (the contract's named
 * alternative to `role="button"`, which would conflict with the row's own implicit
 * ARIA `row` role inside a `table`/`grid`).
 *
 * The Model column shows the raw model_id verbatim (already a human-readable string,
 * e.g. "openai/gpt-4o" — unlike an opaque key UUID, no display-name lookup is needed,
 * hence no `modelsById` prop). The Key column resolves through `keysById` (fail-open:
 * an unresolved id falls back to the raw id, never a blank cell).
 *
 * `blocked` is NOT rendered in this table: the actual FROZEN+shipped logs-explorer-api
 * list response (LogListItem in gateway/logs/api/logs_query_router.py) is METADATA-ONLY
 * and does not carry guardrail_verdict (or a derived `blocked` flag) at the list level
 * — only GET /admin/logs/{id} does. Blocked-call visibility ships in full via the
 * drawer's Guardrail Verdict panel (M5); the table's Status badge instead reflects the
 * one status signal the list response actually carries: status_code.
 */

import { Badge, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Empty } from "@/components/ui";
import { formatNumber, formatTimestamp, formatUsd } from "@/lib/format";

export interface LogListItem {
  id: string;
  created_at: string;
  model_id: string;
  key_id: string;
  status_code: number;
  cost_usd: string | null;
  stream: boolean;
  cached: boolean;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
}

export interface LogsTableProps {
  items: LogListItem[];
  /** UI-side key_id -> display-name resolution (§0 Ground Assumption #2 default). */
  keysById: Record<string, string>;
  onRowActivate: (id: string) => void;
  hasMore: boolean;
  onNext: () => void;
  onPrevious: () => void;
  canGoPrevious: boolean;
}

/** "Xp / Yc / Zt" — each field independently "—" on a pre-metering NULL, never 0. */
function formatTokens(item: LogListItem): string {
  const { prompt_tokens, completion_tokens, total_tokens } = item;
  if (prompt_tokens === null && completion_tokens === null && total_tokens === null) {
    return "—";
  }
  return `${formatNumber(prompt_tokens)}p / ${formatNumber(completion_tokens)}c / ${formatNumber(total_tokens)}t`;
}

function formatLatency(latencyMs: number | null): string {
  return latencyMs === null ? "—" : `${formatNumber(latencyMs)} ms`;
}

export function LogsTable({
  items,
  keysById,
  onRowActivate,
  hasMore,
  onNext,
  onPrevious,
  canGoPrevious,
}: LogsTableProps) {
  if (items.length === 0) {
    return <Empty title="No logs match these filters" />;
  }

  return (
    <div className="flex flex-col gap-3">
      <Table data-slot="logs-table" aria-label="Request logs">
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead>Key</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Latency</TableHead>
            <TableHead>Tokens</TableHead>
            <TableHead>Cost</TableHead>
            <TableHead>When</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const ok = item.status_code < 400;
            const keyLabel = keysById[item.key_id] ?? item.key_id;
            return (
              <TableRow
                key={item.id}
                tabIndex={0}
                aria-label={`Log ${item.model_id} · ${formatTimestamp(item.created_at)}`}
                onClick={(e) => {
                  e.currentTarget.focus();
                  onRowActivate(item.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onRowActivate(item.id);
                  }
                }}
                className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
              >
                <TableCell>{item.model_id}</TableCell>
                <TableCell>{keyLabel}</TableCell>
                <TableCell>
                  <Badge variant={ok ? "success" : "destructive"}>{item.status_code}</Badge>
                </TableCell>
                <TableCell>{formatLatency(item.latency_ms)}</TableCell>
                <TableCell>{formatTokens(item)}</TableCell>
                <TableCell>{formatUsd(item.cost_usd)}</TableCell>
                <TableCell>{formatTimestamp(item.created_at)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <div className="flex items-center justify-end gap-3 px-1 text-sm">
        <button
          type="button"
          onClick={onPrevious}
          disabled={!canGoPrevious}
          className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!hasMore}
          className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Next
        </button>
      </div>
    </div>
  );
}
