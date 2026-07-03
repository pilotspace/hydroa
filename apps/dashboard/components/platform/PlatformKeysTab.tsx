"use client";

/**
 * PlatformKeysTab — Keys tab (M8) of the tenant-detail screen: create/rotate/
 * revoke API keys for the TARGET tenant. SAFETY-CRITICAL (R6): the real,
 * frozen CreateKeyResponse/rotate-response DTOs carry a plaintext `key` field
 * (keys/api/schemas.py:211, "shown EXACTLY ONCE") — this surface must NEVER
 * read, render, or retain it, unconditionally (MILESTONE.md Out-of-scope
 * line). CreateKeyResult/RotateKeyResult below deliberately OMIT the `key`
 * field from their TypeScript shape: even a future `resp.key` access would be
 * a compile error, not merely a code-review miss (§3 CONTRACT's own ⚠ flag,
 * mitigated here with a real test — see test_create_key_never_renders_
 * plaintext_secret / test_rotate_key_never_renders_plaintext_secret).
 *
 * KeyRow.tsx is NOT reused here — it only renders a Revoke action, but this
 * tab also needs Rotate per-row (platform-keys.test.tsx's own
 * test_rotate_key_never_renders_plaintext_secret fires a `name: /rotate/i`
 * button directly from the row, no extra dialog step) and KeyRow.tsx is out
 * of this task's declared Scope to edit. CreateKeyDialog.tsx IS reused
 * unchanged — it only collects a name and calls back via onSubmit; it never
 * reads the mutation's response, so it carries no R6 risk itself.
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import {
  Button,
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  Loading,
  ErrorState,
  Empty,
  Badge,
  Success,
} from "@/components/ui";
import { CreateKeyDialog } from "@/components/keys/CreateKeyDialog";
import { useFocusTrap } from "@/lib/use-focus-trap";

interface PlatformApiKey {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
}

/** R6: `key` (plaintext, shown exactly once) is deliberately absent from this
 * type — see file header. */
interface CreateKeyResult {
  key_id: string;
  name: string;
}

/** R6: `key`/`superseded_key_id`'s own secret are deliberately absent — see
 * file header. */
interface RotateKeyResult {
  new_key_id: string;
  superseded_key_id: string;
  name: string;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export interface PlatformKeysTabProps {
  tenantId: string;
}

export function PlatformKeysTab({ tenantId }: PlatformKeysTabProps) {
  const queryClient = useQueryClient();
  const queryKey = React.useMemo(() => ["platform-tenant-keys", tenantId], [tenantId]);

  const {
    data: keys,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<PlatformApiKey[]>({
    queryKey,
    queryFn: () => bffGet<PlatformApiKey[]>(`/admin/platform/tenants/${tenantId}/keys`),
    retry: false,
  });

  const [isCreateOpen, setIsCreateOpen] = React.useState(false);
  const [created, setCreated] = React.useState<{ name: string; key_id: string } | null>(null);
  const [rotated, setRotated] = React.useState<{ name: string; new_key_id: string } | null>(null);
  const [rotateError, setRotateError] = React.useState<string | null>(null);
  const [revokeTargetId, setRevokeTargetId] = React.useState<string | null>(null);
  const [revokeError, setRevokeError] = React.useState<string | null>(null);

  const createKeyMutation = useMutation({
    mutationFn: (name: string) =>
      bffPost<CreateKeyResult>(`/admin/platform/tenants/${tenantId}/keys`, { name }),
    onSuccess: (resp) => {
      // Only `.name`/`.key_id` are ever read off the response — `.key` is
      // unreachable at the type level (R6). Never spread/stringify the raw
      // response anywhere. `.key_id` is shown truncated, never a fabricated
      // `prefix` (the create/rotate DTOs carry no prefix field — TASK.md §5
      // Known-problem fix (c)); the row's real prefix appears once the
      // invalidated list below refetches.
      setCreated({ name: resp.name, key_id: resp.key_id });
      setRotated(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const rotateKeyMutation = useMutation({
    mutationFn: (keyId: string) =>
      bffPost<RotateKeyResult>(`/admin/platform/tenants/${tenantId}/keys/${keyId}/rotate`, {}),
    onSuccess: (resp) => {
      setRotateError(null);
      setRotated({ name: resp.name, new_key_id: resp.new_key_id });
      setCreated(null);
      void queryClient.invalidateQueries({ queryKey });
    },
    onError: (err) => {
      // R4 — a failed mutation is never silent, mirrored from the Config/Budget
      // tabs' own inline mutError pattern (an earlier gap here was caught by an
      // independent adversarial review before this task closed).
      setRotateError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  const revokeKeyMutation = useMutation({
    mutationFn: (keyId: string) => bffDelete(`/admin/platform/tenants/${tenantId}/keys/${keyId}`),
    onSuccess: () => {
      setRevokeError(null);
      setRevokeTargetId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
    onError: (err) => {
      // Keep the confirm dialog OPEN on failure (do not clear revokeTargetId) so
      // the superadmin sees the error and can retry or cancel explicitly — R4.
      setRevokeError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  async function handleCreateKey(name: string) {
    await createKeyMutation.mutateAsync(name);
  }

  function handleConfirmRevoke() {
    if (revokeTargetId) {
      setRevokeError(null);
      revokeKeyMutation.mutate(revokeTargetId);
    }
  }
  function handleCancelRevoke() {
    setRevokeTargetId(null);
    setRevokeError(null);
  }
  const revokeTrapRef = useFocusTrap<HTMLDivElement>(!!revokeTargetId, handleCancelRevoke);

  if (isLoading) {
    return <Loading label="Loading API keys" className="animate-pulse" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />;
  }

  const list = keys ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button type="button" onClick={() => setIsCreateOpen(true)}>
          Create key
        </Button>
      </div>

      {created && (
        <Success
          title="Key created"
          description={`"${created.name}" (${created.key_id.slice(0, 8)}…) is ready to use.`}
        />
      )}
      {rotated && (
        <Success
          title="Key rotated"
          description={`"${rotated.name}" (${rotated.new_key_id.slice(0, 8)}…) has a new secret.`}
        />
      )}
      {rotateError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {rotateError}
        </p>
      )}

      {list.length === 0 ? (
        <Empty title="No API keys yet" description="Create the first key for this tenant." />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Key ID</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Prefix</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((k) => {
                  const isRevoked = k.revoked_at !== null;
                  const isPendingRevoke = revokeTargetId === k.key_id;
                  return (
                    <TableRow key={k.key_id}>
                      <TableCell className="font-mono text-xs">{k.key_id.slice(0, 8)}…</TableCell>
                      <TableCell>{k.name}</TableCell>
                      <TableCell className="font-mono text-xs">{k.prefix}</TableCell>
                      <TableCell>{new Date(k.created_at).toLocaleDateString()}</TableCell>
                      <TableCell>
                        {isRevoked ? (
                          <Badge variant="destructive">Revoked {k.revoked_at}</Badge>
                        ) : (
                          <Badge variant="secondary">active</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        {!isRevoked && (
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={rotateKeyMutation.isPending}
                              onClick={() => rotateKeyMutation.mutate(k.key_id)}
                            >
                              Rotate
                            </Button>
                            {!isPendingRevoke && (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => setRevokeTargetId(k.key_id)}
                              >
                                Revoke
                              </Button>
                            )}
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {revokeTargetId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          data-testid="revoke-overlay"
        >
          <div
            ref={revokeTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm revocation"
            className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
          >
            <p className="text-sm text-foreground">
              Are you sure you want to revoke this key? This cannot be undone.
            </p>
            {revokeError && (
              <p role="alert" aria-live="polite" className="mt-2 text-sm text-destructive">
                {revokeError}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <Button type="button" variant="destructive" onClick={handleConfirmRevoke}>
                Confirm
              </Button>
              <Button type="button" variant="outline" onClick={handleCancelRevoke}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}

      <CreateKeyDialog
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        onSubmit={handleCreateKey}
      />
    </div>
  );
}
