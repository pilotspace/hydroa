"use client";

/**
 * KeysPage — authenticated key management page
 *
 * Auth guard: middleware.ts handles cookie-presence check server-side.
 * If this page renders, the session cookie is present.
 *
 * States: loading (spinner), empty, error (problem+json title), success (table)
 * Actions: create key (dialog → plaintext banner), revoke key (confirm dialog)
 *          governance editor (PATCH per-key governance fields)
 *          rotate key (POST rotate → one-time plaintext banner)
 * Logout: POST /api/auth/logout then router.push("/login")
 *
 * All data calls use bff-client.ts (credentials:"include") — no Authorization
 * header is ever constructed or read client-side.
 */

import { Fragment, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import { KeyRow } from "./KeyRow";
import { CreateKeyDialog } from "./CreateKeyDialog";
import { PlaintextKeyBanner } from "./PlaintextKeyBanner";
import { KeyGovernanceEditor, ApiKeyGovernance } from "./KeyGovernanceEditor";

/** Extended ApiKey includes governance fields from key-governance §3 FROZEN contract */
interface ApiKey {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
  // Governance fields (nullable — pre-governance keys have null)
  monthly_budget_usd?: string | null;
  soft_budget_usd?: string | null;
  expires_at?: string | null;
  model_allowlist?: string[] | null;
}

interface CreateKeyResponse {
  key_id: string;
  name: string;
  key: string;
}

/** Normalise ApiKey to the full ApiKeyGovernance shape for KeyGovernanceEditor */
function toGovernanceKey(k: ApiKey): ApiKeyGovernance {
  return {
    key_id: k.key_id,
    name: k.name,
    prefix: k.prefix,
    created_at: k.created_at,
    revoked_at: k.revoked_at,
    monthly_budget_usd: k.monthly_budget_usd ?? null,
    soft_budget_usd: k.soft_budget_usd ?? null,
    expires_at: k.expires_at ?? null,
    model_allowlist: k.model_allowlist ?? null,
  };
}

export function KeysPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [plaintextKey, setPlaintextKey] = useState<string | null>(null);
  const [revokeTargetId, setRevokeTargetId] = useState<string | null>(null);
  const [expandedGovernance, setExpandedGovernance] = useState<string | null>(null);

  // Keys query — uses bffGet (credentials:"include") through the catch-all BFF proxy
  const {
    data: keys,
    isLoading,
    isError,
    error,
  } = useQuery<ApiKey[]>({
    queryKey: ["admin-keys"],
    queryFn: () => bffGet<ApiKey[]>("/admin/keys"),
  });

  // Create key mutation
  const createKeyMutation = useMutation({
    mutationFn: (name: string) =>
      bffPost<CreateKeyResponse>("/admin/keys", { name }),
    onSuccess: (data) => {
      setPlaintextKey(data.key);
      void queryClient.invalidateQueries({ queryKey: ["admin-keys"] });
    },
  });

  // Revoke key mutation
  const revokeKeyMutation = useMutation({
    mutationFn: (keyId: string) => bffDelete(`/admin/keys/${keyId}`),
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
    setPlaintextKey(null);
  }

  async function handleLogout() {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // Ignore network errors on logout — redirect regardless
    }
    router.push("/login");
  }

  async function handleCreateKey(name: string) {
    await createKeyMutation.mutateAsync(name);
  }

  function handleGovernanceUpdated() {
    // Refresh the keys list after governance update or rotation
    void queryClient.invalidateQueries({ queryKey: ["admin-keys"] });
  }

  // Get error title from BFF error
  function getErrorTitle(err: unknown): string {
    if (err instanceof BffError) return err.problem.title;
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

      {/* One-time plaintext key banner (create) */}
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
              <Fragment key={key.key_id}>
                <KeyRow
                  apiKey={key}
                  onRevoke={handleRevoke}
                  isPendingRevoke={revokeTargetId === key.key_id}
                />
                {/* Governance editor — its own sibling row (KeyRow renders a <tr>) */}
                <tr>
                  <td colSpan={6}>
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedGovernance(
                          expandedGovernance === key.key_id ? null : key.key_id
                        )
                      }
                    >
                      {expandedGovernance === key.key_id
                        ? "Hide governance"
                        : "Governance"}
                    </button>
                    {expandedGovernance === key.key_id && (
                      <KeyGovernanceEditor
                        apiKey={toGovernanceKey(key)}
                        onUpdated={handleGovernanceUpdated}
                      />
                    )}
                  </td>
                </tr>
              </Fragment>
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
