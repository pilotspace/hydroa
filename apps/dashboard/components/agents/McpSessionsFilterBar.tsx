"use client";

/**
 * McpSessionsFilterBar — agents-console TASK.md §3 CONTRACT M6. Offers every field
 * `LogsFilterBar` offers EXCEPT Model (from/to/key/status/cost — the §3 CONTRACT's own
 * enumeration omits Model, since the new exact "server::tool" field is this tab's own
 * replacement for it: MCP trace rows are namespaced `mcp::<server_host>::<tool_name>`
 * in the `model_id` column, so a free-text catalog dropdown built for chat model ids
 * would mislead here) plus the ONE new exact-match "server::tool" text field, mapped
 * 1:1 onto the same `model_id` query param `LogsFilterBar`'s Model dropdown would use.
 *
 * Same idiom as `LogsFilterBar` throughout: every change forwarded verbatim via
 * `onChange`; the parent (`McpSessionsPanel`) decides validity (R9) and supplies
 * `fieldErrors`; a native `<select>` for Status (same jsdom-testability rationale
 * `LogsFilterBar`'s own module doc states).
 */

import { Input } from "@/components/ui";

export interface LogsFiltersLike {
  from?: string;
  to?: string;
  keyId?: string;
  status?: "all" | "success" | "error";
  costMin?: string;
  costMax?: string;
}

export interface McpSessionsFilters extends LogsFiltersLike {
  /** exact "mcp::<server_host>::<tool_name>" string -> mapped to model_id */
  serverTool?: string;
}

export interface McpSessionsFilterBarProps {
  value: McpSessionsFilters;
  onChange: (next: McpSessionsFilters) => void;
  keys: { id: string; label: string }[];
  fieldErrors?: Partial<Record<keyof McpSessionsFilters, string>>;
  disabled?: boolean;
}

const ALL_KEYS = "__all__";
const NATIVE_SELECT_CLASS =
  "rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function McpSessionsFilterBar({
  value,
  onChange,
  keys,
  fieldErrors = {},
  disabled = false,
}: McpSessionsFilterBarProps) {
  function patch(next: Partial<McpSessionsFilters>) {
    onChange({ ...value, ...next });
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-sessions-filter-from" className="text-xs font-medium text-muted-foreground">
            From
          </label>
          <Input
            id="mcp-sessions-filter-from"
            type="datetime-local"
            value={value.from ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ from: e.target.value || undefined })}
            aria-invalid={fieldErrors.from ? true : undefined}
            aria-describedby={fieldErrors.from ? "mcp-sessions-filter-from-error" : undefined}
            className="w-56"
          />
          {fieldErrors.from ? (
            <p id="mcp-sessions-filter-from-error" role="alert" className="text-xs text-destructive">
              {fieldErrors.from}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-sessions-filter-to" className="text-xs font-medium text-muted-foreground">
            To
          </label>
          <Input
            id="mcp-sessions-filter-to"
            type="datetime-local"
            value={value.to ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ to: e.target.value || undefined })}
            aria-invalid={fieldErrors.to ? true : undefined}
            aria-describedby={fieldErrors.to ? "mcp-sessions-filter-to-error" : undefined}
            className="w-56"
          />
          {fieldErrors.to ? (
            <p id="mcp-sessions-filter-to-error" role="alert" className="text-xs text-destructive">
              {fieldErrors.to}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-sessions-filter-key" className="text-xs font-medium text-muted-foreground">
            Key
          </label>
          <select
            id="mcp-sessions-filter-key"
            aria-label="Key"
            value={value.keyId ?? ALL_KEYS}
            disabled={disabled}
            onChange={(e) => patch({ keyId: e.target.value === ALL_KEYS ? undefined : e.target.value })}
            className={`${NATIVE_SELECT_CLASS} w-48`}
          >
            <option value={ALL_KEYS}>All keys</option>
            {keys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-sessions-filter-status" className="text-xs font-medium text-muted-foreground">
            Status
          </label>
          <select
            id="mcp-sessions-filter-status"
            aria-label="Status"
            value={value.status ?? "all"}
            disabled={disabled}
            onChange={(e) => patch({ status: e.target.value as McpSessionsFilters["status"] })}
            className={`${NATIVE_SELECT_CLASS} w-40`}
          >
            <option value="all">All</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-sessions-filter-cost-min" className="text-xs font-medium text-muted-foreground">
            Min $
          </label>
          <Input
            id="mcp-sessions-filter-cost-min"
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={value.costMin ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ costMin: e.target.value || undefined })}
            aria-invalid={fieldErrors.costMin ? true : undefined}
            className="w-24"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-sessions-filter-cost-max" className="text-xs font-medium text-muted-foreground">
            Max $
          </label>
          <Input
            id="mcp-sessions-filter-cost-max"
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={value.costMax ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ costMax: e.target.value || undefined })}
            aria-invalid={fieldErrors.costMax ? true : undefined}
            className="w-24"
          />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="mcp-sessions-filter-server-tool" className="text-xs font-medium text-muted-foreground">
          Server::tool (exact)
        </label>
        <Input
          id="mcp-sessions-filter-server-tool"
          type="text"
          placeholder="mcp::mcp.acme.example::search"
          value={value.serverTool ?? ""}
          disabled={disabled}
          onChange={(e) => patch({ serverTool: e.target.value || undefined })}
          className="w-96"
        />
      </div>
    </div>
  );
}
