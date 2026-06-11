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
import { bffPatch, bffPost, BffError } from "@/lib/bff-client";
import { PlaintextKeyBanner } from "./PlaintextKeyBanner";

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
    } = {
      monthly_budget_usd: monthlyVal,
      soft_budget_usd: softVal,
      expires_at: expiresAt.trim() === "" ? null : expiresAt.trim(),
      model_allowlist: allowlistItems.length === 0 ? null : allowlistItems,
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

  return (
    <div data-testid="key-governance-editor">
      {/* One-time rotation plaintext banner */}
      {newPlaintextKey && (
        <PlaintextKeyBanner
          plaintextKey={newPlaintextKey}
          onDismiss={handleDismissBanner}
        />
      )}

      {/* Rotate confirm dialog */}
      {showRotateConfirm && (
        <div role="dialog" aria-modal="true" aria-label="Confirm key rotation">
          <p>
            Rotating this key will supersede the current secret. The new secret
            will be shown once. Continue?
          </p>
          <button
            type="button"
            onClick={() => void handleConfirmRotate()}
            disabled={isRotating}
          >
            Confirm
          </button>
          <button
            type="button"
            onClick={() => setShowRotateConfirm(false)}
            disabled={isRotating}
          >
            Cancel
          </button>
        </div>
      )}

      <h3>Governance — {apiKey.name}</h3>

      {/* Rotate button */}
      {!showRotateConfirm && (
        <button
          type="button"
          onClick={() => {
            setRotateError(null);
            setShowRotateConfirm(true);
          }}
        >
          Rotate
        </button>
      )}

      {/* Rotate error surface */}
      {rotateError && (
        <p role="alert">{rotateError}</p>
      )}

      {/* Governance form */}
      <form onSubmit={(e) => void handleSave(e)}>
        {/* Monthly budget (per-key — field name MUST be monthly_budget_usd) */}
        <div>
          <label htmlFor={`monthly-budget-${apiKey.key_id}`}>
            Monthly Budget (USD)
          </label>
          <input
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
        <div>
          <label htmlFor={`soft-budget-${apiKey.key_id}`}>
            Soft Budget (USD)
          </label>
          <input
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
        <div>
          <label htmlFor={`expires-at-${apiKey.key_id}`}>
            Expires At
          </label>
          <input
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
        <div>
          <label htmlFor={`model-allowlist-${apiKey.key_id}`}>
            Model Allowlist
          </label>
          <div>
            <input
              id={`model-allowlist-${apiKey.key_id}`}
              data-testid="model-allowlist-input"
              type="text"
              aria-label="Model allowlist"
              placeholder="Model ID (e.g. openai/gpt-4o)"
              value={allowlistInput}
              onChange={(e) => setAllowlistInput(e.target.value)}
              onKeyDown={handleAllowlistKeyDown}
            />
            <button
              type="button"
              data-testid="add-model-button"
              onClick={handleAddModel}
            >
              Add
            </button>
          </div>
          {/* Chips */}
          <ul>
            {allowlistItems.map((model, i) => (
              <li key={`${model}-${i}`}>
                <span>{model}</span>
                <button
                  type="button"
                  aria-label={`Remove ${model}`}
                  onClick={() => handleRemoveModel(i)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* Client-side validation error */}
        {validationError && (
          <p role="alert">{validationError}</p>
        )}

        {/* API error (403 / 422 inline surface) */}
        {apiError && (
          <p role="alert">{apiError}</p>
        )}

        <button type="submit" disabled={isSubmitting}>
          Save
        </button>
      </form>

      {/* Show last-saved monthly_budget_usd as text so it is queryable by getByText */}
      {savedMonthlyBudget !== null && !apiError && (
        <p data-testid="current-monthly-budget">{savedMonthlyBudget}</p>
      )}
    </div>
  );
}
