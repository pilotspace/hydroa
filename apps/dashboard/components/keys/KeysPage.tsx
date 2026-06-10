"use client";

/**
 * KeysPage — authenticated key management page
 *
 * Auth guard: checks localStorage token on mount; if absent or expired,
 * calls router.push("/login") immediately before rendering content.
 *
 * States: loading (spinner), empty, error (problem+json title), success (table)
 * Actions: create key (dialog → plaintext banner), revoke key (confirm dialog)
 */

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getToken, clearToken, isTokenValid } from "@/lib/auth";
import { apiGet, apiPost, apiDelete, ApiError } from "@/lib/api-client";
import { KeyRow } from "./KeyRow";
import { CreateKeyDialog } from "./CreateKeyDialog";
import { PlaintextKeyBanner } from "./PlaintextKeyBanner";

interface ApiKey {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
}

interface CreateKeyResponse {
  key_id: string;
  name: string;
  key: string;
}

export function KeysPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [isAuthChecked, setIsAuthChecked] = useState(false);
  const [isAuthed, setIsAuthed] = useState(false);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [plaintextKey, setPlaintextKey] = useState<string | null>(null);
  const [revokeTargetId, setRevokeTargetId] = useState<string | null>(null);

  // Auth guard — runs synchronously on mount before any render of protected content
  useEffect(() => {
    const token = getToken();
    if (!isTokenValid(token)) {
      clearToken();
      router.push("/login");
      setIsAuthChecked(true);
      setIsAuthed(false);
    } else {
      setIsAuthChecked(true);
      setIsAuthed(true);
    }
  }, [router]);

  // Keys query — only enabled when auth is confirmed
  const {
    data: keys,
    isLoading,
    isError,
    error,
  } = useQuery<ApiKey[]>({
    queryKey: ["admin-keys"],
    queryFn: () => apiGet<ApiKey[]>("/admin/keys"),
    enabled: isAuthed,
  });

  // Create key mutation
  const createKeyMutation = useMutation({
    mutationFn: (name: string) =>
      apiPost<CreateKeyResponse>("/admin/keys", { name }),
    onSuccess: (data) => {
      setPlaintextKey(data.key);
      void queryClient.invalidateQueries({ queryKey: ["admin-keys"] });
    },
  });

  // Revoke key mutation
  const revokeKeyMutation = useMutation({
    mutationFn: (keyId: string) => apiDelete(`/admin/keys/${keyId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-keys"] });
      setRevokeTargetId(null);
    },
  });

  function handleRevoke(keyId: string) {
    setRevokeTargetId(keyId);
  }

  function handleConfirmRevoke() {
    if (revokeTargetId) {
      revokeKeyMutation.mutate(revokeTargetId);
    }
  }

  function handleCancelRevoke() {
    setRevokeTargetId(null);
  }

  function handleDismissBanner() {
    // Safety: clear plaintext key from state immediately
    setPlaintextKey(null);
  }

  function handleLogout() {
    clearToken();
    router.push("/login");
  }

  async function handleCreateKey(name: string) {
    await createKeyMutation.mutateAsync(name);
  }

  // Before auth check resolves, render nothing (no flash of protected content)
  if (!isAuthChecked) {
    return null;
  }

  // Not authed — redirect already fired, render nothing
  if (!isAuthed) {
    return null;
  }

  // Get error title from API error
  function getErrorTitle(err: unknown): string {
    if (err instanceof ApiError) return err.problem.title;
    if (err instanceof Error) return err.message;
    return "An error occurred";
  }

  return (
    <div>
      <header>
        <h1>API Keys</h1>
        <div>
          {!isCreateDialogOpen && (
            <button
              type="button"
              onClick={() => setIsCreateDialogOpen(true)}
            >
              Create key
            </button>
          )}
          <button type="button" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </header>

      {/* One-time plaintext key banner */}
      {plaintextKey && (
        <PlaintextKeyBanner
          plaintextKey={plaintextKey}
          onDismiss={handleDismissBanner}
        />
      )}

      {/* Revoke confirmation dialog */}
      {revokeTargetId && (
        <div role="dialog" aria-modal="true" aria-label="Confirm revocation">
          <p>Are you sure you want to revoke this key? This cannot be undone.</p>
          <button type="button" onClick={handleConfirmRevoke}>
            Confirm
          </button>
          <button type="button" onClick={handleCancelRevoke}>
            Cancel
          </button>
        </div>
      )}

      {/* Keys list */}
      {isLoading && (
        <div
          role="status"
          aria-busy="true"
          aria-label="Loading API keys"
          data-testid="loading"
        >
          <span className="animate-pulse">Loading…</span>
        </div>
      )}

      {isError && !isLoading && (
        <p role="alert">{getErrorTitle(error)}</p>
      )}

      {!isLoading && !isError && keys !== undefined && keys.length === 0 && (
        <p>No API keys yet. Create your first key to get started.</p>
      )}

      {!isLoading && !isError && keys !== undefined && keys.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Key ID</th>
              <th>Name</th>
              <th>Prefix</th>
              <th>Created</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <KeyRow
                key={key.key_id}
                apiKey={key}
                onRevoke={handleRevoke}
                isPendingRevoke={revokeTargetId === key.key_id}
              />
            ))}
          </tbody>
        </table>
      )}

      <CreateKeyDialog
        isOpen={isCreateDialogOpen}
        onClose={() => setIsCreateDialogOpen(false)}
        onSubmit={handleCreateKey}
      />
    </div>
  );
}
