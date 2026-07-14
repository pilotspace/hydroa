"use client";

/**
 * CreateAgentDialog — agents-console TASK.md §3 CONTRACT M3. Hand-rolled inline overlay
 * dialog (mirrors keys/CreateKeyDialog.tsx's own convention — not the Radix-based
 * ui/dialog.tsx primitive — so jsdom tests can interact without showModal() support).
 *
 * name (required) / owner (optional, a <select> sourced from GET /admin/users) /
 * monthly_budget_usd, rpm_limit, tpm_limit (optional numeric inputs) -> POST
 * /admin/agents. A 409 (agent_principal_name_conflict) surfaces beside Name; a 422
 * (invalid_request) surfaces beside the budget input — R2/R3. Either way the dialog
 * stays open and every OTHER entered field is preserved (component-owned state is
 * never reset on a rejected submit).
 */

import { useState, type FormEvent } from "react";
import { bffPost, BffError } from "@/lib/bff-client";
import { Input, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import type { AgentPrincipal } from "./AgentIdentityCard";

export interface TenantUserOption {
  id: string;
  email: string;
}

export interface CreateAgentDialogProps {
  open: boolean;
  onClose: () => void;
  /** appends to the query cache, no refetch required */
  onCreated: (agent: AgentPrincipal) => void;
  users: TenantUserOption[];
}

interface CreateAgentBody {
  name: string;
  owner_user_id?: string;
  monthly_budget_usd?: string;
  rpm_limit?: number;
  tpm_limit?: number;
}

const NO_OWNER = "__no_owner__";

export function CreateAgentDialog({ open, onClose, onCreated, users }: CreateAgentDialogProps) {
  const [name, setName] = useState("");
  const [ownerId, setOwnerId] = useState(NO_OWNER);
  const [budget, setBudget] = useState("");
  const [rpm, setRpm] = useState("");
  const [tpm, setTpm] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function reset() {
    setName("");
    setOwnerId(NO_OWNER);
    setBudget("");
    setRpm("");
    setTpm("");
    setNameError(null);
    setBudgetError(null);
    setGlobalError(null);
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setNameError(null);
    setBudgetError(null);
    setGlobalError(null);

    if (name.trim() === "") {
      setNameError("Name is required");
      return;
    }

    const body: CreateAgentBody = { name: name.trim() };
    if (ownerId !== NO_OWNER) body.owner_user_id = ownerId;
    if (budget.trim() !== "") body.monthly_budget_usd = budget.trim();
    if (rpm.trim() !== "") body.rpm_limit = Number(rpm.trim());
    if (tpm.trim() !== "") body.tpm_limit = Number(tpm.trim());

    setIsSubmitting(true);
    try {
      const created = await bffPost<AgentPrincipal>("/admin/agents", body);
      onCreated(created);
      reset();
      onClose();
    } catch (err) {
      if (err instanceof BffError && err.status === 409) {
        setNameError(err.problem.title ?? "This name is already in use");
      } else if (err instanceof BffError && err.status === 422) {
        setBudgetError(err.problem.title ?? "Invalid value");
      } else if (err instanceof BffError) {
        setGlobalError(err.problem.title ?? "Failed to create agent");
      } else {
        setGlobalError("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(open, handleClose);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4">
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-agent-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="create-agent-title" className="text-lg font-semibold text-foreground">
            New agent
          </h2>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="agent-name-input" className="text-sm font-medium text-foreground">
              Name
            </label>
            <Input
              id="agent-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="off"
              aria-invalid={nameError ? true : undefined}
              aria-describedby={nameError ? "agent-name-error" : undefined}
            />
            {nameError ? (
              <p id="agent-name-error" role="alert" aria-live="polite" className="text-sm text-destructive">
                {nameError}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="agent-owner-select" className="text-sm font-medium text-foreground">
              Owner
            </label>
            <select
              id="agent-owner-select"
              value={ownerId}
              onChange={(e) => setOwnerId(e.target.value)}
              className="h-10 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value={NO_OWNER}>No owner</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="agent-budget-input" className="text-sm font-medium text-foreground">
              Monthly budget (USD)
            </label>
            <Input
              id="agent-budget-input"
              type="number"
              step="0.01"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="No cap"
              aria-invalid={budgetError ? true : undefined}
              aria-describedby={budgetError ? "agent-budget-error" : undefined}
            />
            {budgetError ? (
              <p id="agent-budget-error" role="alert" aria-live="polite" className="text-sm text-destructive">
                {budgetError}
              </p>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="agent-rpm-input" className="text-sm font-medium text-foreground">
                RPM limit
              </label>
              <Input
                id="agent-rpm-input"
                type="number"
                value={rpm}
                onChange={(e) => setRpm(e.target.value)}
                placeholder="No limit"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="agent-tpm-input" className="text-sm font-medium text-foreground">
                TPM limit
              </label>
              <Input
                id="agent-tpm-input"
                type="number"
                value={tpm}
                onChange={(e) => setTpm(e.target.value)}
                placeholder="No limit"
              />
            </div>
          </div>

          {globalError ? (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          ) : null}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Creating…" : "Create"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
