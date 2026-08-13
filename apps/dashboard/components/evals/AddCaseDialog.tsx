"use client";

/**
 * AddCaseDialog — evals-console TASK.md §3 CONTRACT (authoring surface). POST
 * /admin/evals/sets/{id}/cases body { request_body:object, assertion:object }. Both
 * fields are FROZEN as opaque JSON objects (no fixed schema of their own), so this
 * dialog collects each as free-form JSON text and Zod-validates it parses to a
 * (non-array) object client-side before ever calling the BFF — the same
 * hand-rolled-dialog + useFocusTrap + Zod-input convention as CreateSetDialog/
 * CreateTeamDialog.
 */

import { useState, type FormEvent } from "react";
import { z } from "zod";
import { bffPost, BffError } from "@/lib/bff-client";
import { Textarea, Button } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import type { EvalCase } from "./types";

const JsonObjectField = z
  .string()
  .trim()
  .min(1, "Required")
  .superRefine((val, ctx) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(val);
    } catch {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Must be valid JSON" });
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "Must be a JSON object" });
    }
  });

const AddCaseSchema = z.object({
  requestBody: JsonObjectField,
  assertion: JsonObjectField,
});

export interface AddCaseDialogProps {
  open: boolean;
  onClose: () => void;
  setId: string;
  onCreated: (evalCase: EvalCase) => void;
}

export function AddCaseDialog({ open, onClose, setId, onCreated }: AddCaseDialogProps) {
  const [requestBody, setRequestBody] = useState("");
  const [assertion, setAssertion] = useState("");
  const [requestBodyError, setRequestBodyError] = useState<string | null>(null);
  const [assertionError, setAssertionError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function reset() {
    setRequestBody("");
    setAssertion("");
    setRequestBodyError(null);
    setAssertionError(null);
    setGlobalError(null);
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setRequestBodyError(null);
    setAssertionError(null);
    setGlobalError(null);

    const result = AddCaseSchema.safeParse({ requestBody, assertion });
    if (!result.success) {
      for (const issue of result.error.issues) {
        if (issue.path[0] === "requestBody") setRequestBodyError(issue.message);
        if (issue.path[0] === "assertion") setAssertionError(issue.message);
      }
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await bffPost<EvalCase>(`/admin/evals/sets/${setId}/cases`, {
        request_body: JSON.parse(result.data.requestBody),
        assertion: JSON.parse(result.data.assertion),
      });
      onCreated(created);
      reset();
      onClose();
    } catch (err) {
      if (err instanceof BffError) setGlobalError(err.problem.title ?? "Failed to add case");
      else setGlobalError("An unexpected error occurred");
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
        aria-labelledby="add-eval-case-title"
        className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="add-eval-case-title" className="text-lg font-semibold text-foreground">
            Add case
          </h2>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="eval-case-request-body-input" className="text-sm font-medium text-foreground">
              Request body (JSON)
            </label>
            <Textarea
              id="eval-case-request-body-input"
              value={requestBody}
              onChange={(e) => setRequestBody(e.target.value)}
              rows={4}
              className="font-mono text-xs"
              aria-invalid={requestBodyError ? true : undefined}
              aria-describedby={requestBodyError ? "eval-case-request-body-error" : undefined}
            />
            {requestBodyError ? (
              <p id="eval-case-request-body-error" role="alert" aria-live="polite" className="text-sm text-destructive">
                {requestBodyError}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="eval-case-assertion-input" className="text-sm font-medium text-foreground">
              Assertion (JSON)
            </label>
            <Textarea
              id="eval-case-assertion-input"
              value={assertion}
              onChange={(e) => setAssertion(e.target.value)}
              rows={4}
              className="font-mono text-xs"
              placeholder='{"kind":"exact_match","expected":"…"}'
              aria-invalid={assertionError ? true : undefined}
              aria-describedby={assertionError ? "eval-case-assertion-error" : undefined}
            />
            {assertionError ? (
              <p id="eval-case-assertion-error" role="alert" aria-live="polite" className="text-sm text-destructive">
                {assertionError}
              </p>
            ) : null}
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
              {isSubmitting ? "Adding…" : "Add case"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
