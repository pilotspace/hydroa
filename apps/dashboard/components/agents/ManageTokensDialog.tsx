"use client";

/**
 * ManageTokensDialog — agents-console TASK.md §3 CONTRACT M5, CR-B resolved
 * (agent-identity-governance §3 FROZEN @ v2 M13).
 *
 * A REAL picker for DETACH: GET /admin/agents/{id}/tokens enumerates the tokens
 * CURRENTLY attached to this principal (id/name/created_at/revoked_at/
 * access_expires_at — never a secret), each row offering its own Detach action.
 * There is still no tenant-wide enumeration of UNATTACHED tokens anywhere in the
 * frozen surface (Ground Issue 2's residual half) — so a NEW attachment keeps an
 * explicit token-id field, disclosed in-dialog, not hidden. This reconciles CR-B's
 * "real picker" freeze decision with the actual shape of the new read endpoint
 * (attached-only, not tenant-wide).
 *
 * Every response renders inline inside the dialog (role="alert"); the dialog never
 * closes on error (R4).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bffDelete, bffGet, bffPost, BffError } from "@/lib/bff-client";
import { Button, ErrorState, Input, Loading } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { formatTimestamp } from "@/lib/format";

export interface AgentTokenInfo {
  id: string;
  /** maps to the token's OAuth scope (no free-text label column on an RFC-8628 mint) */
  name: string;
  created_at: string;
  revoked_at: string | null;
  access_expires_at: string;
}

export interface ManageTokensDialogProps {
  open: boolean;
  /** null = closed */
  principalId: string | null;
  onClose: () => void;
}

function tokensQueryKey(principalId: string) {
  return ["agent-tokens", principalId];
}

export function ManageTokensDialog({ open, principalId, onClose }: ManageTokensDialogProps) {
  const queryClient = useQueryClient();
  const [tokenIdInput, setTokenIdInput] = useState("");
  const [attachError, setAttachError] = useState<string | null>(null);
  const [detachError, setDetachError] = useState<string | null>(null);

  const tokensQuery = useQuery<AgentTokenInfo[]>({
    queryKey: tokensQueryKey(principalId ?? ""),
    queryFn: () => bffGet<AgentTokenInfo[]>(`/admin/agents/${principalId}/tokens`),
    enabled: open && principalId !== null,
    retry: false,
  });

  const attachMutation = useMutation({
    mutationFn: (tokenId: string) =>
      bffPost(`/admin/agents/${principalId}/tokens/${tokenId}/attach`, {}),
    onSuccess: () => {
      setAttachError(null);
      setTokenIdInput("");
      void queryClient.invalidateQueries({ queryKey: tokensQueryKey(principalId ?? "") });
      void queryClient.invalidateQueries({ queryKey: ["admin-agents"] });
    },
    onError: (err) => {
      if (err instanceof BffError) setAttachError(err.problem.title ?? "Failed to attach token");
      else setAttachError("An unexpected error occurred");
    },
  });

  const detachMutation = useMutation({
    mutationFn: (tokenId: string) => bffDelete(`/admin/agents/${principalId}/tokens/${tokenId}`),
    onSuccess: (_data, tokenId) => {
      setDetachError(null);
      queryClient.setQueryData<AgentTokenInfo[]>(tokensQueryKey(principalId ?? ""), (prev) =>
        (prev ?? []).filter((t) => t.id !== tokenId),
      );
      void queryClient.invalidateQueries({ queryKey: ["admin-agents"] });
    },
    onError: (err) => {
      if (err instanceof BffError) setDetachError(err.problem.title ?? "Failed to detach token");
      else setDetachError("An unexpected error occurred");
    },
  });

  function handleClose() {
    setTokenIdInput("");
    setAttachError(null);
    setDetachError(null);
    onClose();
  }

  function handleAttach() {
    setAttachError(null);
    const trimmed = tokenIdInput.trim();
    if (trimmed === "") {
      setAttachError("Enter a token id");
      return;
    }
    attachMutation.mutate(trimmed);
  }

  const trapRef = useFocusTrap<HTMLDivElement>(open, handleClose);

  if (!open || principalId === null) return null;

  const tokens = tokensQuery.data ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4">
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="manage-tokens-title"
        className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <div className="flex flex-col gap-4">
          <h2 id="manage-tokens-title" className="text-lg font-semibold text-foreground">
            Manage tokens
          </h2>

          {tokensQuery.isLoading ? (
            <Loading label="Loading tokens…" />
          ) : tokensQuery.isError ? (
            <ErrorState
              title={tokensQuery.error instanceof BffError ? tokensQuery.error.problem.title : "Failed to load tokens"}
            />
          ) : tokens.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tokens attached yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {tokens.map((token) => (
                <li
                  key={token.id}
                  className="flex items-center justify-between gap-3 rounded-md border border-border p-2.5 text-sm"
                >
                  <div className="flex flex-col">
                    <span className="font-medium text-foreground">{token.name}</span>
                    <span className="text-xs text-muted-foreground">
                      Attached {formatTimestamp(token.created_at)} · expires {formatTimestamp(token.access_expires_at)}
                    </span>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={detachMutation.isPending}
                    onClick={() => detachMutation.mutate(token.id)}
                  >
                    Detach
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {detachError ? (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {detachError}
            </p>
          ) : null}

          <div className="flex flex-col gap-1.5 border-t border-border pt-4">
            <label htmlFor="attach-token-id-input" className="text-sm font-medium text-foreground">
              Token id
            </label>
            <p className="text-xs text-muted-foreground">
              Paste a token id to attach it — there is no directory of unattached tokens yet.
            </p>
            <div className="flex gap-2">
              <Input
                id="attach-token-id-input"
                type="text"
                value={tokenIdInput}
                onChange={(e) => setTokenIdInput(e.target.value)}
                autoComplete="off"
                aria-invalid={attachError ? true : undefined}
                aria-describedby={attachError ? "attach-token-error" : undefined}
              />
              <Button type="button" onClick={handleAttach} disabled={attachMutation.isPending}>
                {attachMutation.isPending ? "Attaching…" : "Attach"}
              </Button>
            </div>
            {attachError ? (
              <p id="attach-token-error" role="alert" aria-live="polite" className="text-sm text-destructive">
                {attachError}
              </p>
            ) : null}
          </div>

          <div className="flex justify-end">
            <Button type="button" variant="outline" onClick={handleClose}>
              Close
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
