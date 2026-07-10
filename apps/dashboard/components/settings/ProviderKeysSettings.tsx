"use client";

/**
 * ProviderKeysSettings — "Provider Keys" tab content for the /settings hub (OWNER-only).
 *
 * Lists all six BYOK providers with their configured/enabled/auth_mode/updated_at status, and
 * lets a tenant OWNER configure (PUT) or delete (DELETE) a provider credential. The gateway
 * NEVER returns a stored secret, so this UI never echoes one — secrets live only in the
 * write-only ConfigureProviderDialog while it is open.
 *
 * GET /admin/provider-keys → { keys: ProviderKeyStatus[] }  (403 → ErrorState, no controls)
 *
 * Mirrors OidcSettings (query/error handling) + KeysPage (parent owns the mutation;
 * confirm-before-delete dialog).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, bffDelete, BffError } from "@/lib/bff-client";
import { Button, Loading, ErrorState } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { ConfigureProviderDialog } from "./ConfigureProviderDialog";

/** The seven BYOK providers, in display order. */
const PROVIDERS = [
  "openrouter",
  "openai",
  "anthropic",
  "google",
  "bedrock",
  "azure",
  "minimax",
] as const;

/**
 * Friendly labels. Chosen so no label is a substring of another's matcher — e.g. azure
 * is "Azure" (NOT "Azure OpenAI") so a test querying /openai/i never matches the azure row.
 */
const DISPLAY: Record<string, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  bedrock: "Bedrock",
  azure: "Azure",
  minimax: "MiniMax",
};

interface ProviderKeyStatus {
  provider: string;
  configured: boolean;
  enabled: boolean;
  auth_mode: string | null;
  updated_at: string;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function ProviderKeysSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<{ keys: ProviderKeyStatus[] }>({
    queryKey: ["admin-provider-keys"],
    queryFn: () => bffGet<{ keys: ProviderKeyStatus[] }>("/admin/provider-keys"),
    retry: false,
  });

  const [configuring, setConfiguring] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // The parent owns the save mutation (§3 contract); the dialog only builds the body and
  // surfaces a BffError inline. onSuccess closes the modal (unmount → secrets discarded).
  const saveMutation = useMutation({
    mutationFn: ({ provider, body }: { provider: string; body: Record<string, unknown> }) =>
      bffPut<ProviderKeyStatus>(`/admin/provider-keys/${provider}`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-provider-keys"] });
      setConfiguring(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (provider: string) => bffDelete(`/admin/provider-keys/${provider}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-provider-keys"] });
      setDeleting(null);
    },
    onError: () => {
      // Delete is idempotent server-side (404 → already gone). Close the confirm and let the
      // list refetch reflect reality rather than surfacing a transient inline error.
      setDeleting(null);
    },
  });

  // ESC/focus-trap for the delete-confirm dialog (accessible-dialog contract).
  const confirmTrapRef = useFocusTrap<HTMLDivElement>(!!deleting, () => setDeleting(null));

  if (isLoading) {
    return <Loading label="Loading provider keys" />;
  }

  // 403 (owner-only) and any other GET error → ErrorState, NO configure controls.
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  const byProvider = new Map<string, ProviderKeyStatus>(
    (data?.keys ?? []).map((k) => [k.provider, k])
  );

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Bring your own provider credentials. Keys are encrypted at rest and never shown again
        after they are saved.
      </p>

      <ul className="flex flex-col gap-3">
        {PROVIDERS.map((provider) => {
          const st = byProvider.get(provider);
          const configured = !!st?.configured;
          return (
            <li
              key={provider}
              data-provider={provider}
              className="flex items-center justify-between gap-4 rounded-lg border border-border bg-card p-4"
            >
              <div className="flex flex-col gap-1">
                <span className="text-sm font-medium text-foreground">{DISPLAY[provider]}</span>
                <span className="flex flex-wrap items-center gap-2 text-xs">
                  <span className={configured ? "text-foreground" : "text-muted-foreground"}>
                    {configured ? "Configured" : "Not configured"}
                  </span>
                  {configured && st?.auth_mode && (
                    <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                      {st.auth_mode}
                    </span>
                  )}
                  {configured && st && !st.enabled && (
                    <span className="text-muted-foreground">(disabled)</span>
                  )}
                  {configured && st?.updated_at && (
                    <span className="text-muted-foreground">
                      Updated {st.updated_at.slice(0, 10)}
                    </span>
                  )}
                </span>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  aria-label={`Configure ${provider}`}
                  onClick={() => setConfiguring(provider)}
                >
                  Configure
                </Button>
                {configured && (
                  <Button
                    type="button"
                    variant="destructive"
                    aria-label={`Delete ${provider}`}
                    onClick={() => setDeleting(provider)}
                  >
                    Delete
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {/* Configure / replace dialog — mounted only while configuring → fresh empty form. */}
      {configuring && (
        <ConfigureProviderDialog
          provider={configuring}
          isOpen
          onClose={() => setConfiguring(null)}
          onSubmit={async (body) => {
            await saveMutation.mutateAsync({ provider: configuring, body });
          }}
        />
      )}

      {/* Delete-confirm dialog. */}
      {deleting && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          data-testid="delete-provider-overlay"
        >
          <div
            ref={confirmTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm provider key deletion"
            className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
          >
            <p className="text-sm text-foreground">
              Delete the stored credential for {DISPLAY[deleting] ?? deleting}? Requests for this
              provider will fail until a new key is configured.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="destructive"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(deleting)}
              >
                Confirm
              </Button>
              <Button type="button" variant="outline" onClick={() => setDeleting(null)}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
