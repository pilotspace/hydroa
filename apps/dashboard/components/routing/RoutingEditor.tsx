"use client";

/**
 * RoutingEditor — owner/admin-only editor section for /routing.
 *
 * Reads routing_strategy + deployments from the already-loaded GET /admin/routing
 * response (passed in as props from RoutingPage, which owns the query).
 * Writes PUT /admin/routing with { routing_strategy, model_groups: object-form rows }.
 *
 * Design decisions:
 * - Seed-during-render guard (no setState in effect) mirrors GuardrailSettings.
 * - Inline BffError title via getErrorTitle (identical pattern).
 * - retry:false on the parent query; mutation error surfaced inline, form preserved.
 * - PUT body sends model_groups as OBJECT-form rows (model_id+weight+rpm_limit+tpm_limit)
 *   — NEVER bare strings, so weight/limit edits are never silently dropped by the backend.
 * - invalidateQueries(["admin-routing"]) after a successful save.
 * - Restart notice is plain text (not color-only, a11y).
 * - Every input/select has an associated label (getByLabelText compatible).
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bffPut, BffError } from "@/lib/bff-client";
import { Button, Input } from "@/components/ui";

// ── Types ──────────────────────────────────────────────────────────────────────

export type RoutingStrategy = "ordered" | "simple-shuffle" | "least-busy" | "latency";

export interface DeploymentRow {
  model_id: string;
  weight: number;
  rpm_limit: number | null;
  tpm_limit: number | null;
}

export interface RoutingEditorProps {
  /** Stable server-fetched values used to seed local state once per load. */
  serverStrategy: RoutingStrategy;
  serverDeployments: Record<string, DeploymentRow[]>;
}

interface PutRoutingBody {
  routing_strategy: RoutingStrategy;
  model_groups: Record<string, DeploymentRow[]>;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) {
    // The gateway forwards the underlying validator code in `detail` (e.g.
    // UNKNOWN_ROUTING_STRATEGY, INVALID_DEPLOYMENT_WEIGHT). Surface it so a 422 is
    // ACTIONABLE — the operator sees WHICH field failed, not just a generic title.
    // (`detail` is present at runtime though not in the shared ProblemDetail type.)
    const detail = (err.problem as { detail?: string }).detail;
    return detail ? `${err.problem.title}: ${detail}` : err.problem.title;
  }
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

const STRATEGY_OPTIONS: { value: RoutingStrategy; label: string }[] = [
  { value: "ordered", label: "Ordered" },
  { value: "simple-shuffle", label: "Simple shuffle" },
  { value: "least-busy", label: "Least busy" },
  { value: "latency", label: "Latency" },
];

function emptyRow(): DeploymentRow {
  return { model_id: "", weight: 1, rpm_limit: null, tpm_limit: null };
}

// ── Component ─────────────────────────────────────────────────────────────────

export function RoutingEditor({ serverStrategy, serverDeployments }: RoutingEditorProps) {
  const queryClient = useQueryClient();

  // ── Local editable state ────────────────────────────────────────────────────
  const [strategy, setStrategy] = useState<RoutingStrategy>(serverStrategy);
  const [groups, setGroups] = useState<Record<string, DeploymentRow[]>>(serverDeployments);
  const [newAlias, setNewAlias] = useState("");
  const [mutError, setMutError] = useState<string | null>(null);
  // v37 routing-editor-feedback: client validation error (fast-fail, no PUT) + a transient
  // post-save "restart to apply" confirmation that clears the moment editing resumes.
  const [clientError, setClientError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // ── Seed-during-render guard ────────────────────────────────────────────────
  // Mirrors GuardrailSettings: fires only when the server reference changes
  // (initial load + after a successful save reinstalls via setQueryData).
  // Converges in one render, never loops.
  const [seededStrategy, setSeededStrategy] = useState<RoutingStrategy | undefined>(undefined);
  const [seededDeployments, setSeededDeployments] = useState<
    Record<string, DeploymentRow[]> | undefined
  >(undefined);

  if (serverStrategy !== seededStrategy) {
    setSeededStrategy(serverStrategy);
    setStrategy(serverStrategy);
  }
  if (serverDeployments !== seededDeployments) {
    setSeededDeployments(serverDeployments);
    setGroups(serverDeployments);
  }

  // ── Mutation ───────────────────────────────────────────────────────────────
  const saveRouting = useMutation({
    mutationFn: (body: PutRoutingBody) => bffPut<PutRoutingBody>("/admin/routing", body),
    onSuccess: () => {
      setMutError(null);
      setSaved(true); // show the transient "Saved — restart to apply" confirmation
      void queryClient.invalidateQueries({ queryKey: ["admin-routing"] });
    },
    onError: (err) => {
      setMutError(getErrorTitle(err));
    },
  });

  // v37: any user edit invalidates the transient "saved" confirmation and dismisses a stale
  // client error — but NOT the programmatic reseed after a save's refetch (that path never
  // calls this, so the post-save affordance survives the invalidate→refetch round-trip).
  function markEdited() {
    if (saved) setSaved(false);
    if (clientError) setClientError(null);
  }

  // ── Group handlers ─────────────────────────────────────────────────────────

  function handleAddGroup() {
    markEdited();
    const alias = newAlias.trim();
    if (!alias || alias in groups) return;
    setGroups((prev) => ({ ...prev, [alias]: [emptyRow()] }));
    setNewAlias("");
  }

  function handleRemoveGroup(alias: string) {
    markEdited();
    setGroups((prev) => {
      const next = { ...prev };
      delete next[alias];
      return next;
    });
  }

  function handleAddRow(alias: string) {
    markEdited();
    setGroups((prev) => ({
      ...prev,
      [alias]: [...prev[alias], emptyRow()],
    }));
  }

  function handleRemoveRow(alias: string, idx: number) {
    markEdited();
    setGroups((prev) => ({
      ...prev,
      [alias]: prev[alias].filter((_, i) => i !== idx),
    }));
  }

  function handleRowChange(
    alias: string,
    idx: number,
    field: keyof DeploymentRow,
    rawValue: string,
  ) {
    markEdited();
    setGroups((prev) => {
      const rows = prev[alias].map((row, i) => {
        if (i !== idx) return row;
        if (field === "model_id") return { ...row, model_id: rawValue };
        if (field === "weight") return { ...row, weight: rawValue === "" ? 0 : Number(rawValue) };
        // rpm_limit / tpm_limit: empty string → null
        const numVal = rawValue === "" ? null : Number(rawValue);
        return { ...row, [field]: numVal };
      });
      return { ...prev, [alias]: rows };
    });
  }

  // ── Save ───────────────────────────────────────────────────────────────────

  function handleSave() {
    setMutError(null);
    // v37 client guard: every deployment needs a model_id and a weight > 0. Fast-fail before the
    // PUT so an empty/zero weight (handleRowChange coerces "" → 0) never costs a 422 round-trip.
    // The server still backstops anything this guard does not cover.
    const invalid = Object.values(groups)
      .flat()
      .some((row) => row.model_id.trim() === "" || !(row.weight > 0));
    if (invalid) {
      setSaved(false);
      setClientError("Every deployment needs a model and a weight greater than 0.");
      return;
    }
    setClientError(null);
    saveRouting.mutate({ routing_strategy: strategy, model_groups: groups });
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <section aria-labelledby="routing-editor-heading" className="flex flex-col gap-6">
      <div>
        <h2
          id="routing-editor-heading"
          className="text-lg font-semibold tracking-tight text-foreground"
        >
          Edit routing configuration
        </h2>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Changes take effect on next restart. Applying requires a service restart.
        </p>
      </div>

      {/* Strategy select */}
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="routing-strategy"
          className="text-sm font-medium text-foreground"
        >
          Routing strategy
        </label>
        <select
          id="routing-strategy"
          aria-label="Routing strategy"
          value={strategy}
          onChange={(e) => {
            markEdited();
            setStrategy(e.target.value as RoutingStrategy);
          }}
          className="w-56 rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {STRATEGY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Deployment groups editor */}
      <div className="flex flex-col gap-4">
        <h3 className="text-sm font-semibold text-foreground">Deployment groups</h3>

        {Object.entries(groups).map(([alias, rows]) => (
          <fieldset
            key={alias}
            className="flex flex-col gap-3 rounded-lg border border-border p-4"
          >
            <div className="flex items-center justify-between">
              <legend className="px-1 text-sm font-semibold text-foreground">{alias}</legend>
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Remove group ${alias}`}
                onClick={() => handleRemoveGroup(alias)}
              >
                Remove group
              </Button>
            </div>

            {rows.map((row, idx) => {
              const n = idx + 1;
              return (
                <div key={idx} className="grid grid-cols-[1fr_80px_80px_80px_auto] items-end gap-2">
                  {/* model_id */}
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor={`model-id-${alias}-${n}`}
                      className="text-xs font-medium text-muted-foreground"
                    >
                      {`Model ${alias} ${n}`}
                    </label>
                    <Input
                      id={`model-id-${alias}-${n}`}
                      aria-label={`Model ${alias} ${n}`}
                      placeholder="model_id"
                      value={row.model_id}
                      onChange={(e) => handleRowChange(alias, idx, "model_id", e.target.value)}
                    />
                  </div>

                  {/* weight */}
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor={`weight-${alias}-${n}`}
                      className="text-xs font-medium text-muted-foreground"
                    >
                      {`Weight ${alias} ${n}`}
                    </label>
                    <Input
                      id={`weight-${alias}-${n}`}
                      aria-label={`Weight ${alias} ${n}`}
                      type="number"
                      min={0}
                      placeholder="1"
                      value={row.weight}
                      onChange={(e) => handleRowChange(alias, idx, "weight", e.target.value)}
                    />
                  </div>

                  {/* rpm_limit */}
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor={`rpm-${alias}-${n}`}
                      className="text-xs font-medium text-muted-foreground"
                    >
                      {`RPM ${alias} ${n}`}
                    </label>
                    <Input
                      id={`rpm-${alias}-${n}`}
                      aria-label={`RPM ${alias} ${n}`}
                      type="number"
                      min={0}
                      placeholder="—"
                      value={row.rpm_limit ?? ""}
                      onChange={(e) => handleRowChange(alias, idx, "rpm_limit", e.target.value)}
                    />
                  </div>

                  {/* tpm_limit */}
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor={`tpm-${alias}-${n}`}
                      className="text-xs font-medium text-muted-foreground"
                    >
                      {`TPM ${alias} ${n}`}
                    </label>
                    <Input
                      id={`tpm-${alias}-${n}`}
                      aria-label={`TPM ${alias} ${n}`}
                      type="number"
                      min={0}
                      placeholder="—"
                      value={row.tpm_limit ?? ""}
                      onChange={(e) => handleRowChange(alias, idx, "tpm_limit", e.target.value)}
                    />
                  </div>

                  {/* remove row */}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Remove deployment ${n} from ${alias}`}
                    onClick={() => handleRemoveRow(alias, idx)}
                  >
                    ×
                  </Button>
                </div>
              );
            })}

            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => handleAddRow(alias)}
              >
                Add deployment
              </Button>
            </div>
          </fieldset>
        ))}

        {/* Add new alias */}
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="new-alias"
              className="text-sm font-medium text-foreground"
            >
              New alias
            </label>
            <Input
              id="new-alias"
              aria-label="New alias"
              placeholder="e.g. fast"
              value={newAlias}
              onChange={(e) => setNewAlias(e.target.value)}
            />
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={handleAddGroup}
            aria-label="Add group"
          >
            Add group
          </Button>
        </div>
      </div>

      {/* Client validation error (fast-fail, no PUT) — v37 */}
      {clientError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {clientError}
        </p>
      )}

      {/* Mutation error inline */}
      {mutError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {mutError}
        </p>
      )}

      {/* Transient post-save confirmation — clears on the next edit (v37) */}
      {saved && (
        <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
          Saved — restart the gateway to apply.
        </p>
      )}

      {/* Save */}
      <div>
        <Button
          type="button"
          disabled={saveRouting.isPending}
          onClick={handleSave}
        >
          {saveRouting.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </section>
  );
}
