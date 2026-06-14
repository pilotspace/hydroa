"use client";

/**
 * KeyGovernanceEditor — per-key governance form
 *
 * Allows owner/admin to set/clear:
 *   - monthly_budget_usd  (per-key; NOT "budget_usd_monthly" which is the tenant budget)
 *   - soft_budget_usd
 *   - expires_at          (ISO-8601 date-time string or null)
 *   - model_allowlist     (array of model ID strings or null)
 *
 * Calls PATCH /api/gw/admin/keys/{key_id} through the BFF catch-all proxy.
 * No Authorization header is ever constructed client-side.
 *
 * Rotation: POST /api/gw/admin/keys/{key_id}/rotate → 201 → shows plaintext
 * once via PlaintextKeyBanner, then calls onUpdated() to signal parent refresh.
 *
 * Client-side validation (blocks API call):
 *   R1: soft_budget_usd > monthly_budget_usd → inline error
 *   R2: negative monthly_budget_usd or soft_budget_usd → inline error
 *   R3: model_allowlist containing empty-string entries → inline error
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, bffPatch, bffPost, BffError } from "@/lib/bff-client";
import { PlaintextKeyBanner } from "./PlaintextKeyBanner";
import { Button, Input, Switch } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";

export interface ApiKeyGovernance {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
  monthly_budget_usd: string | null;
  soft_budget_usd: string | null;
  expires_at: string | null;
  model_allowlist: string[] | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  team_id: string | null;
  cache_enabled: boolean;
}

interface Team {
  id: string;
  name: string;
}

interface RotateResponse {
  new_key_id: string;
  superseded_key_id: string;
  key: string;
  name: string;
  monthly_budget_usd: string | null;
  soft_budget_usd: string | null;
  expires_at: string | null;
  model_allowlist: string[] | null;
}

interface KeyGovernanceEditorProps {
  apiKey: ApiKeyGovernance;
  onUpdated: (updated?: ApiKeyGovernance) => void;
}

export function KeyGovernanceEditor({ apiKey, onUpdated }: KeyGovernanceEditorProps) {
  // Form field state — initialise from prop
  const [monthlyBudget, setMonthlyBudget] = useState<string>(
    apiKey.monthly_budget_usd ?? ""
  );
  const [softBudget, setSoftBudget] = useState<string>(
    apiKey.soft_budget_usd ?? ""
  );
  const [expiresAt, setExpiresAt] = useState<string>(
    apiKey.expires_at ?? ""
  );
  const [allowlistItems, setAllowlistItems] = useState<string[]>(
    apiKey.model_allowlist ?? []
  );
  const [allowlistInput, setAllowlistInput] = useState<string>("");

  // Rate-limit / team / cache state
  const [rpmLimit, setRpmLimit] = useState<string>(apiKey.rpm_limit?.toString() ?? "");
  const [tpmLimit, setTpmLimit] = useState<string>(apiKey.tpm_limit?.toString() ?? "");
  const [teamId, setTeamId] = useState<string>(apiKey.team_id ?? "");
  const [cacheEnabled, setCacheEnabled] = useState<boolean>(apiKey.cache_enabled ?? false);

  // Teams dropdown — tolerate loading/error (no crash)
  const { data: teams } = useQuery<Team[]>({
    queryKey: ["admin-teams"],
    queryFn: () => bffGet<Team[]>("/admin/teams"),
    retry: false,
  });

  // UI state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  // Last-saved display values — shown as text after a successful save so tests
  // and users can confirm the persisted value without reading from an input.
  const [savedMonthlyBudget, setSavedMonthlyBudget] = useState<string | null>(
    apiKey.monthly_budget_usd
  );

  // Rotation state
  const [showRotateConfirm, setShowRotateConfirm] = useState(false);
  const [isRotating, setIsRotating] = useState(false);
  const [rotateError, setRotateError] = useState<string | null>(null);
  const [newPlaintextKey, setNewPlaintextKey] = useState<string | null>(null);

  // ── Allowlist helpers ────────────────────────────────────────────────────────

  function handleAddModel() {
    const trimmed = allowlistInput.trim();
    if (!trimmed) return;
    setAllowlistItems((prev) => [...prev, trimmed]);
    setAllowlistInput("");
  }

  function handleRemoveModel(index: number) {
    setAllowlistItems((prev) => prev.filter((_, i) => i !== index));
  }

  function handleAllowlistKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddModel();
    }
  }

  // ── Governance save ──────────────────────────────────────────────────────────

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setValidationError(null);
    setApiError(null);

    // R3: empty-string entries in model_allowlist block the API call
    if (allowlistItems.some((m) => m.trim() === "")) {
      setValidationError("Model allowlist entries cannot be empty.");
      return;
    }

    // R4: rpm_limit / tpm_limit must be a positive integer if provided
    const rpmTrimmed = rpmLimit.trim();
    if (rpmTrimmed !== "" && (!/^\d+$/.test(rpmTrimmed) || parseInt(rpmTrimmed, 10) <= 0)) {
      setValidationError("RPM limit must be a positive integer.");
      return;
    }
    const tpmTrimmed = tpmLimit.trim();
    if (tpmTrimmed !== "" && (!/^\d+$/.test(tpmTrimmed) || parseInt(tpmTrimmed, 10) <= 0)) {
      setValidationError("TPM limit must be a positive integer.");
      return;
    }

    // Parse budget values
    const monthlyVal = monthlyBudget.trim() === "" ? null : monthlyBudget.trim();
    const softVal = softBudget.trim() === "" ? null : softBudget.trim();

    // R2: negative values block API call
    if (monthlyVal !== null && parseFloat(monthlyVal) < 0) {
      setValidationError("Budget must be non-negative.");
      return;
    }
    if (softVal !== null && parseFloat(softVal) < 0) {
      setValidationError("Budget must be non-negative.");
      return;
    }

    // R1: soft > hard blocks API call (both non-null)
    if (monthlyVal !== null && softVal !== null) {
      if (parseFloat(softVal) > parseFloat(monthlyVal)) {
        setValidationError("Soft budget cannot exceed hard budget.");
        return;
      }
    }

    const body: {
      monthly_budget_usd: string | null;
      soft_budget_usd: string | null;
      expires_at: string | null;
      model_allowlist: string[] | null;
      rpm_limit: number | null;
      tpm_limit: number | null;
      team_id: string | null;
      cache_enabled: boolean;
    } = {
      monthly_budget_usd: monthlyVal,
      soft_budget_usd: softVal,
      expires_at: expiresAt.trim() === "" ? null : expiresAt.trim(),
      model_allowlist: allowlistItems.length === 0 ? null : allowlistItems,
      rpm_limit: rpmTrimmed === "" ? null : parseInt(rpmTrimmed, 10),
      tpm_limit: tpmTrimmed === "" ? null : parseInt(tpmTrimmed, 10),
      team_id: teamId === "" ? null : teamId,
      cache_enabled: cacheEnabled,
    };

    setIsSubmitting(true);
    try {
      const updated = await bffPatch<ApiKeyGovernance>(
        `/admin/keys/${apiKey.key_id}`,
        body
      );
      onUpdated(updated);
      // Reflect updated values in form fields
      setMonthlyBudget(updated.monthly_budget_usd ?? "");
      setSoftBudget(updated.soft_budget_usd ?? "");
      setExpiresAt(updated.expires_at ?? "");
      setAllowlistItems(updated.model_allowlist ?? []);
      setRpmLimit(updated.rpm_limit?.toString() ?? "");
      setTpmLimit(updated.tpm_limit?.toString() ?? "");
      setTeamId(updated.team_id ?? "");
      setCacheEnabled(updated.cache_enabled ?? false);
      // Update displayed saved value so screen text reflects persisted state
      setSavedMonthlyBudget(updated.monthly_budget_usd);
    } catch (err) {
      if (err instanceof BffError) {
        setApiError(err.problem.title ?? `Error ${err.status}`);
      } else {
        setApiError("An unexpected error occurred.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  // ── Rotation ─────────────────────────────────────────────────────────────────

  async function handleConfirmRotate() {
    setRotateError(null);
    setIsRotating(true);
    try {
      const result = await bffPost<RotateResponse>(
        `/admin/keys/${apiKey.key_id}/rotate`,
        {}
      );
      // Show plaintext once via banner
      setNewPlaintextKey(result.key);
      // Signal parent to refresh list
      onUpdated();
      setShowRotateConfirm(false);
    } catch (err) {
      if (err instanceof BffError) {
        setRotateError(err.problem.title ?? `Error ${err.status}`);
      } else {
        setRotateError("An unexpected error occurred.");
      }
      setShowRotateConfirm(false);
    } finally {
      setIsRotating(false);
    }
  }

  function handleDismissBanner() {
    setNewPlaintextKey(null);
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  // Focus-trap + ESC for the rotate-confirm dialog (accessible-dialog contract)
  const rotateTrapRef = useFocusTrap<HTMLDivElement>(showRotateConfirm, () =>
    setShowRotateConfirm(false),
  );

  return (
    <div
      data-testid="key-governance-editor"
      className="mt-2 flex flex-col gap-4 rounded-lg border border-border bg-muted/30 p-4"
    >
      {/* One-time rotation plaintext banner */}
      {newPlaintextKey && (
        <PlaintextKeyBanner
          plaintextKey={newPlaintextKey}
          onDismiss={handleDismissBanner}
        />
      )}

      {/* Rotate confirm dialog */}
      {showRotateConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          data-testid="rotate-overlay"
        >
          <div
            ref={rotateTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm key rotation"
            className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
          >
            <p className="text-sm text-foreground">
              Rotating this key will supersede the current secret. The new secret
              will be shown once. Continue?
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                onClick={() => void handleConfirmRotate()}
                disabled={isRotating}
              >
                Confirm
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowRotateConfirm(false)}
                disabled={isRotating}
              >
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          Governance — {apiKey.name}
        </h3>
        {/* Rotate button */}
        {!showRotateConfirm && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setRotateError(null);
              setShowRotateConfirm(true);
            }}
          >
            Rotate
          </Button>
        )}
      </div>

      {/* Rotate error surface */}
      {rotateError && (
        <p role="alert" className="text-sm text-destructive">
          {rotateError}
        </p>
      )}

      {/* Governance form */}
      <form onSubmit={(e) => void handleSave(e)} className="flex flex-col gap-3">
        {/* Monthly budget (per-key — field name MUST be monthly_budget_usd) */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`monthly-budget-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            Monthly Budget (USD)
          </label>
          <Input
            id={`monthly-budget-${apiKey.key_id}`}
            data-testid="monthly-budget-input"
            type="text"
            inputMode="decimal"
            aria-label="Monthly budget"
            placeholder="Monthly budget (leave empty for unlimited)"
            value={monthlyBudget}
            onChange={(e) => setMonthlyBudget(e.target.value)}
          />
        </div>

        {/* Soft budget */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`soft-budget-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            Soft Budget (USD)
          </label>
          <Input
            id={`soft-budget-${apiKey.key_id}`}
            data-testid="soft-budget-input"
            type="text"
            inputMode="decimal"
            aria-label="Soft budget"
            placeholder="Soft budget (alert threshold)"
            value={softBudget}
            onChange={(e) => setSoftBudget(e.target.value)}
          />
        </div>

        {/* Expires at */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`expires-at-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            Expires At
          </label>
          <Input
            id={`expires-at-${apiKey.key_id}`}
            data-testid="expires-at-input"
            type="text"
            aria-label="Expires at"
            placeholder="Expires at (ISO-8601, e.g. 2099-01-01T00:00:00Z)"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
          />
        </div>

        {/* Model allowlist */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`model-allowlist-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            Model Allowlist
          </label>
          <div className="flex gap-2">
            <Input
              id={`model-allowlist-${apiKey.key_id}`}
              data-testid="model-allowlist-input"
              type="text"
              aria-label="Model allowlist"
              placeholder="Model ID (e.g. openai/gpt-4o)"
              value={allowlistInput}
              onChange={(e) => setAllowlistInput(e.target.value)}
              onKeyDown={handleAllowlistKeyDown}
            />
            <Button
              type="button"
              variant="outline"
              data-testid="add-model-button"
              onClick={handleAddModel}
            >
              Add
            </Button>
          </div>
          {/* Chips */}
          {allowlistItems.length > 0 && (
            <ul className="mt-1 flex flex-wrap gap-2">
              {allowlistItems.map((model, i) => (
                <li
                  key={`${model}-${i}`}
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
                >
                  <span>{model}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${model}`}
                    onClick={() => handleRemoveModel(i)}
                    className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* RPM limit */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`rpm-limit-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            RPM Limit
          </label>
          <Input
            id={`rpm-limit-${apiKey.key_id}`}
            type="text"
            inputMode="numeric"
            aria-label="Requests per minute (RPM) limit"
            placeholder="Requests per minute (leave empty for unlimited)"
            value={rpmLimit}
            onChange={(e) => setRpmLimit(e.target.value)}
          />
        </div>

        {/* TPM limit */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`tpm-limit-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            TPM Limit
          </label>
          <Input
            id={`tpm-limit-${apiKey.key_id}`}
            type="text"
            inputMode="numeric"
            aria-label="Tokens per minute (TPM) limit"
            placeholder="Tokens per minute (leave empty for unlimited)"
            value={tpmLimit}
            onChange={(e) => setTpmLimit(e.target.value)}
          />
        </div>

        {/* Team dropdown */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`team-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            Team
          </label>
          <select
            id={`team-${apiKey.key_id}`}
            aria-label="Team"
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">No team</option>
            {(teams ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>

        {/* Cache switch */}
        <div className="flex items-center justify-between gap-4">
          <label
            htmlFor={`cache-${apiKey.key_id}`}
            className="text-sm font-medium text-foreground"
          >
            Enable response cache
          </label>
          <Switch
            id={`cache-${apiKey.key_id}`}
            aria-label="Enable response cache"
            checked={cacheEnabled}
            onCheckedChange={setCacheEnabled}
          />
        </div>

        {/* Client-side validation error */}
        {validationError && (
          <p role="alert" className="text-sm text-destructive">
            {validationError}
          </p>
        )}

        {/* API error (403 / 422 inline surface) */}
        {apiError && (
          <p role="alert" className="text-sm text-destructive">
            {apiError}
          </p>
        )}

        <Button type="submit" disabled={isSubmitting} className="self-start">
          Save
        </Button>
      </form>

      {/* Show last-saved monthly_budget_usd as text so it is queryable by getByText */}
      {savedMonthlyBudget !== null && !apiError && (
        <p data-testid="current-monthly-budget" className="text-sm text-muted-foreground">
          {savedMonthlyBudget}
        </p>
      )}
    </div>
  );
}
