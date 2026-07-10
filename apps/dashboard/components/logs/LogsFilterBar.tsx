"use client";

/**
 * LogsFilterBar — controlled filter inputs for the Logs Explorer (M2). Every change is
 * forwarded verbatim via `onChange`; LogsExplorerPage decides whether the resulting
 * combination is valid (R3) — this component never blocks typing, it only displays
 * `fieldErrors` the parent computed.
 *
 * Status options are ADAPTED from the frozen §3 draft's five-way bucket
 * (all/success/client_error/server_error/blocked) to the two buckets the actually
 * shipped logs-explorer-api implements (`_STATUS_BUCKETS = ("success", "error")` in
 * gateway/logs/api/logs_query_router.py — a "client_error"/"server_error" split, and a
 * "blocked" filter, do not exist server-side; guardrail_verdict is absent from the list
 * response entirely). This task's own §0 Ground note explicitly authorizes this: "this
 * UI adapts to whichever shape is actually frozen on the sibling contract." Blocked-call
 * visibility still ships in full via the drawer's Guardrail Verdict panel (M5).
 *
 * Model/Key/Status use a plain native `<select>` (GuardrailSettings.tsx's Mode dropdowns
 * and ModelPicker's own catalog dropdown both already do this), NOT the Radix-based
 * `Select`/`SelectTrigger` primitive in ui/select.tsx — that primitive is exported but
 * has zero existing call sites in this app, and Radix Select's popper positioning depends
 * on `hasPointerCapture`/`scrollIntoView`/`ResizeObserver`, none of which jsdom implements,
 * making it untestable here without adding new polyfills the rest of the suite doesn't
 * carry. The native `<select>` is the proven, already-tested pattern for a dropdown in
 * this codebase; a project-wide Radix-Select rollout is out of this task's scope.
 */

import { Input } from "@/components/ui";

export interface LogsFilters {
  from?: string;
  to?: string;
  modelId?: string;
  keyId?: string;
  status?: "all" | "success" | "error";
  costMin?: string;
  costMax?: string;
}

export interface LogsFilterBarProps {
  value: LogsFilters;
  onChange: (next: LogsFilters) => void;
  models: { id: string; label: string }[];
  keys: { id: string; label: string }[];
  fieldErrors?: Partial<Record<keyof LogsFilters, string>>;
  disabled?: boolean;
}

const ALL_MODELS = "__all__";
const ALL_KEYS = "__all__";

const NATIVE_SELECT_CLASS =
  "rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function LogsFilterBar({
  value,
  onChange,
  models,
  keys,
  fieldErrors = {},
  disabled = false,
}: LogsFilterBarProps) {
  function patch(next: Partial<LogsFilters>) {
    onChange({ ...value, ...next });
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="logs-filter-from" className="text-xs font-medium text-muted-foreground">
            From
          </label>
          <Input
            id="logs-filter-from"
            type="datetime-local"
            value={value.from ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ from: e.target.value || undefined })}
            aria-invalid={fieldErrors.from ? true : undefined}
            aria-describedby={fieldErrors.from ? "logs-filter-from-error" : undefined}
            className="w-56"
          />
          {fieldErrors.from ? (
            <p id="logs-filter-from-error" role="alert" className="text-xs text-destructive">
              {fieldErrors.from}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="logs-filter-to" className="text-xs font-medium text-muted-foreground">
            To
          </label>
          <Input
            id="logs-filter-to"
            type="datetime-local"
            value={value.to ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ to: e.target.value || undefined })}
            aria-invalid={fieldErrors.to ? true : undefined}
            aria-describedby={fieldErrors.to ? "logs-filter-to-error" : undefined}
            className="w-56"
          />
          {fieldErrors.to ? (
            <p id="logs-filter-to-error" role="alert" className="text-xs text-destructive">
              {fieldErrors.to}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="logs-filter-model" className="text-xs font-medium text-muted-foreground">
            Model
          </label>
          <select
            id="logs-filter-model"
            aria-label="Model"
            value={value.modelId ?? ALL_MODELS}
            disabled={disabled}
            onChange={(e) => patch({ modelId: e.target.value === ALL_MODELS ? undefined : e.target.value })}
            className={`${NATIVE_SELECT_CLASS} w-48`}
          >
            <option value={ALL_MODELS}>All models</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="logs-filter-key" className="text-xs font-medium text-muted-foreground">
            Key
          </label>
          <select
            id="logs-filter-key"
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
          <label htmlFor="logs-filter-status" className="text-xs font-medium text-muted-foreground">
            Status
          </label>
          <select
            id="logs-filter-status"
            aria-label="Status"
            value={value.status ?? "all"}
            disabled={disabled}
            onChange={(e) => patch({ status: e.target.value as LogsFilters["status"] })}
            className={`${NATIVE_SELECT_CLASS} w-40`}
          >
            <option value="all">All</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="logs-filter-cost-min" className="text-xs font-medium text-muted-foreground">
            Min $
          </label>
          <Input
            id="logs-filter-cost-min"
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={value.costMin ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ costMin: e.target.value || undefined })}
            aria-invalid={fieldErrors.costMin ? true : undefined}
            aria-describedby={fieldErrors.costMin ? "logs-filter-cost-min-error" : undefined}
            className="w-24"
          />
          {fieldErrors.costMin ? (
            <p id="logs-filter-cost-min-error" role="alert" className="text-xs text-destructive">
              {fieldErrors.costMin}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="logs-filter-cost-max" className="text-xs font-medium text-muted-foreground">
            Max $
          </label>
          <Input
            id="logs-filter-cost-max"
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            value={value.costMax ?? ""}
            disabled={disabled}
            onChange={(e) => patch({ costMax: e.target.value || undefined })}
            aria-invalid={fieldErrors.costMax ? true : undefined}
            aria-describedby={fieldErrors.costMax ? "logs-filter-cost-max-error" : undefined}
            className="w-24"
          />
          {fieldErrors.costMax ? (
            <p id="logs-filter-cost-max-error" role="alert" className="text-xs text-destructive">
              {fieldErrors.costMax}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
