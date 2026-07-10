"use client";

/**
 * ScimSettings — SCIM tab content for the /settings hub.
 *
 * GET    /admin/scim/tokens              -> { tokens: ScimTokenRow[] }        (403 -> ErrorState, no affordances — M4)
 * POST   /admin/scim/tokens              body:{name}  -> plaintext token, once (M3)
 * POST   /admin/scim/tokens/{id}/rotate  -> NEW plaintext token, once          (M3)
 * DELETE /admin/scim/tokens/{id}         -> 204
 *
 * Mirrors KeysPage's composition: CreateKeyDialog (name-only, Zod-validated, reused
 * verbatim) + PlaintextKeyBanner (reveal-once, cleared on dismiss — Safety §5, reused
 * verbatim) + ConfirmDialog (revoke/rotate destructive confirm, reused verbatim — its
 * own inline-error-stays-open contract covers the rotate/revoke-on-already-gone-token
 * race scenarios for free, no extra code needed here).
 *
 * M4: the 403 branch is the ONLY gate — a MEMBER lacking MEMBERS_MANAGE never sees a
 * Create/Rotate/Revoke affordance because the whole populated-state JSX below is
 * unreachable on that path (client-side hiding is a UX nicety; the server 403 is the
 * real, authoritative gate — §1 assumption).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import {
  Button,
  Badge,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Loading,
  Empty,
  ErrorState,
} from "@/components/ui";
import { CreateKeyDialog } from "@/components/keys/CreateKeyDialog";
import { PlaintextKeyBanner } from "@/components/keys/PlaintextKeyBanner";
import { ConfirmDialog } from "@/components/teams/ConfirmDialog";

interface ScimTokenRow {
  id: string;
  name: string;
  created_at: string;
  revoked_at: string | null;
}

interface ScimTokenReveal {
  id: string;
  name: string;
  token: string;
  created_at: string;
}

interface PendingAction {
  type: "revoke" | "rotate";
  id: string;
  name: string;
}

const SCIM_QUERY_KEY = ["admin-scim-tokens"];

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function ScimSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<{ tokens: ScimTokenRow[] }>({
    queryKey: SCIM_QUERY_KEY,
    queryFn: () => bffGet<{ tokens: ScimTokenRow[] }>("/admin/scim/tokens"),
    // design-for-failure: a 403 is deterministic — don't retry-storm a settled error
    retry: false,
  });

  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  // Local UI state (not persisted): cleared on PlaintextKeyBanner's onDismiss — NEVER
  // written to the TanStack Query cache, localStorage, or a URL (Safety §5).
  const [plaintextReveal, setPlaintextReveal] = useState<{
    token: string;
    mode: "create" | "rotate";
  } | null>(null);

  function invalidateTokens() {
    void queryClient.invalidateQueries({ queryKey: SCIM_QUERY_KEY });
  }

  const createMutation = useMutation({
    mutationFn: (name: string) => bffPost<ScimTokenReveal>("/admin/scim/tokens", { name }),
    onSuccess: (resp) => {
      setPlaintextReveal({ token: resp.token, mode: "create" });
      invalidateTokens();
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (id: string) => bffPost<ScimTokenReveal>(`/admin/scim/tokens/${id}/rotate`, {}),
    onSuccess: (resp) => {
      setPlaintextReveal({ token: resp.token, mode: "rotate" });
      invalidateTokens();
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => bffDelete(`/admin/scim/tokens/${id}`),
    onSuccess: () => {
      invalidateTokens();
    },
  });

  async function handleCreate(name: string) {
    // CreateKeyDialog awaits this and handles Zod/422/403 errors inline itself
    // (reused verbatim) — a thrown BffError here surfaces there, dialog stays open.
    await createMutation.mutateAsync(name);
  }

  async function handleConfirmPendingAction() {
    if (!pendingAction) return;
    if (pendingAction.type === "revoke") {
      await revokeMutation.mutateAsync(pendingAction.id);
    } else {
      await rotateMutation.mutateAsync(pendingAction.id);
    }
  }

  function handleClosePendingAction() {
    setPendingAction(null);
    // A rotate/revoke race (another admin tab already acted on this token) can leave
    // this tab's list stale even when the action itself failed inline — always resync
    // on dismiss so a stale row never lingers (mirrors the frozen scenario's "the list
    // re-fetches on dismiss").
    invalidateTokens();
  }

  if (isLoading) {
    return <Loading label="Loading SCIM settings" />;
  }

  // M4 / R(403): any GET error (403 ERR_AUTH_FORBIDDEN for a MEMBER, or any other
  // failure incl. a cross-tenant 404) renders ErrorState with NO create/rotate/revoke
  // affordance anywhere on the tab — the populated JSX below is simply never reached.
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  const tokens = data?.tokens ?? [];

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Provision SCIM bearer tokens so your identity provider can create, update, and
        deactivate users automatically.
      </p>

      {plaintextReveal && (
        <PlaintextKeyBanner
          plaintextKey={plaintextReveal.token}
          onDismiss={() => setPlaintextReveal(null)}
        />
      )}

      {tokens.length === 0 ? (
        <Empty
          title="No SCIM tokens yet"
          description="Create your first token to connect your identity provider."
          action={
            <Button type="button" onClick={() => setIsCreateOpen(true)}>
              Create token
            </Button>
          }
        />
      ) : (
        <>
          <div className="flex justify-end">
            <Button type="button" onClick={() => setIsCreateOpen(true)}>
              Create token
            </Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tokens.map((token) => {
                const isRevoked = token.revoked_at !== null;
                const isPending = pendingAction?.id === token.id;
                return (
                  <TableRow key={token.id} role="row">
                    <TableCell className="font-mono text-xs">
                      {token.id.slice(0, 8)}…
                    </TableCell>
                    <TableCell>{token.name}</TableCell>
                    <TableCell>{new Date(token.created_at).toLocaleDateString()}</TableCell>
                    <TableCell>
                      {isRevoked ? (
                        <Badge variant="destructive">Revoked</Badge>
                      ) : (
                        <Badge variant="secondary">active</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {!isRevoked && !isPending && (
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            aria-label={`Rotate ${token.name}`}
                            onClick={() =>
                              setPendingAction({ type: "rotate", id: token.id, name: token.name })
                            }
                          >
                            Rotate
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            aria-label={`Revoke ${token.name}`}
                            onClick={() =>
                              setPendingAction({ type: "revoke", id: token.id, name: token.name })
                            }
                          >
                            Revoke
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </>
      )}

      <CreateKeyDialog isOpen={isCreateOpen} onClose={() => setIsCreateOpen(false)} onSubmit={handleCreate} />

      <ConfirmDialog
        open={!!pendingAction}
        title={pendingAction?.type === "revoke" ? "Revoke SCIM token?" : "Rotate SCIM token?"}
        description={
          pendingAction?.type === "revoke"
            ? `Revoking "${pendingAction.name}" immediately stops your identity provider from using it. This cannot be undone.`
            : pendingAction
              ? `Rotating "${pendingAction.name}" issues a new token and immediately supersedes the old one.`
              : undefined
        }
        confirmLabel={pendingAction?.type === "revoke" ? "Revoke" : "Rotate"}
        onConfirm={handleConfirmPendingAction}
        onClose={handleClosePendingAction}
      />
    </div>
  );
}
