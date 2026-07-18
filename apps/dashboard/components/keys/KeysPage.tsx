"use client";

/**
 * KeysPage — authenticated key management page
 *
 * States: loading (spinner), empty, error (problem+json title), success (table)
 * Actions: create key (dialog → plaintext banner), revoke key (confirm dialog)
 *          governance editor (PATCH per-key governance fields)
 *          rotate key (POST rotate → one-time plaintext banner)
 *
 * Sign-out lives in the shared AppShell sidebar chrome (see components/ui/app-shell),
 * not on this page — every authenticated surface gets one consistent logout control.
 *
 * All data calls use bff-client.ts (credentials:"include") — no Authorization
 * header is ever constructed or read client-side.
 */

import { Fragment, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPost, bffDelete, BffError } from "@/lib/bff-client";
import { KeyRow } from "./KeyRow";
import { CreateKeyDialog } from "./CreateKeyDialog";
import type { Tier } from "./TierSelector";
import { PlaintextKeyBanner } from "./PlaintextKeyBanner";
import { QuickstartPanel } from "./QuickstartPanel";
import { publicApiBaseUrl } from "@/lib/public-api-base-url";
import { KeyGovernanceEditor, ApiKeyGovernance } from "./KeyGovernanceEditor";
import { RatelimitsPanel } from "./RatelimitsPanel";
import { BandwidthPanel } from "./BandwidthPanel";
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
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { useFocusTrap } from "@/lib/use-focus-trap";

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
  // Depth governance fields (v15 governance-completion-ui) — GET /admin/keys returns
  // all four (keys/api/router.py:151-154). They MUST flow into the editor or a
  // no-touch dense-PATCH save would silently clear the key's rpm/tpm/team & cache.
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  team_id?: string | null;
  cache_enabled?: boolean;
  // capture_enabled/tier (audit-remediation) — GET /admin/keys returns both
  // (keys/api/schemas.py KeyInfoResponse); they MUST flow into the editor or a
  // no-touch dense-PATCH save would silently clear the key's real capture/tier.
  capture_enabled?: boolean;
  tier?: string | null;
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
    // Carry the depth fields through so the editor prefills the real values —
    // without these the dense PATCH would clear them on a no-touch save.
    rpm_limit: k.rpm_limit ?? null,
    tpm_limit: k.tpm_limit ?? null,
    team_id: k.team_id ?? null,
    cache_enabled: k.cache_enabled ?? false,
    capture_enabled: k.capture_enabled ?? false,
    tier: k.tier ?? null,
  };
}

export function KeysPage() {
  const queryClient = useQueryClient();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [plaintextKey, setPlaintextKey] = useState<string | null>(null);
  const [revokeTargetId, setRevokeTargetId] = useState<string | null>(null);
  const [expandedGovernance, setExpandedGovernance] = useState<string | null>(null);
  // M9: the tier CreateKeyDialog's TierSelector currently has selected — mirrored here
  // (via onTierChange) so the POST body assembly stays owned by KeysPage without
  // widening CreateKeyDialog's onSubmit(name) signature (a pre-existing, out-of-scope
  // suite asserts that call has exactly one argument).
  const [selectedTier, setSelectedTier] = useState<Tier>("standard");

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

  // Create key mutation — tier (M9) is a required field, always included (defaulting
  // "standard"), field OWNED by service-tiers (cited, not redefined here).
  const createKeyMutation = useMutation({
    mutationFn: ({ name, tier }: { name: string; tier: Tier }) =>
      bffPost<CreateKeyResponse>("/admin/keys", { name, tier }),
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

  async function handleCreateKey(name: string) {
    await createKeyMutation.mutateAsync({ name, tier: selectedTier });
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

  // Focus-trap + ESC for the revoke-confirm dialog (accessible-dialog contract)
  const revokeTrapRef = useFocusTrap<HTMLDivElement>(!!revokeTargetId, handleCancelRevoke);

  // Hero metric — active (non-revoked) key count, derived from the fetched list.
  const activeKeyCount = keys?.filter((k) => !k.revoked_at).length ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="API Keys"
        description="Provision, govern, and rotate the keys that authenticate calls to your gateway."
        actions={
          !isCreateDialogOpen ? (
            <Button type="button" onClick={() => setIsCreateDialogOpen(true)}>
              Create key
            </Button>
          ) : undefined
        }
      />

      {/* One-time plaintext key banner (create) — OUTSIDE the tabs so it persists
          regardless of which tab is active. QuickstartPanel mounts alongside it,
          sharing the SAME in-memory plaintextKey — no new fetch (activation-quickstart M4). */}
      {plaintextKey && (
        <>
          <PlaintextKeyBanner
            plaintextKey={plaintextKey}
            onDismiss={handleDismissBanner}
          />
          <QuickstartPanel plaintextKey={plaintextKey} baseUrl={publicApiBaseUrl()} />
        </>
      )}

      {/* Revoke confirmation dialog — OUTSIDE the tabs (a fixed overlay; its focus-trap
          must remain reachable from any tab). */}
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

      {/* Hero — active key count (only once the list has loaded; never fabricated). */}
      {!isLoading && !isError && keys !== undefined && (
        <div
          data-testid="keys-hero"
          className="rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Active keys
          </p>
          <p className="text-3xl font-semibold text-foreground">{activeKeyCount}</p>
        </div>
      )}

      <Tabs defaultValue="keys" className="flex flex-col gap-4">
        <TabsList>
          <TabsTrigger value="keys">Keys</TabsTrigger>
          <TabsTrigger value="rate-limits">Rate limits</TabsTrigger>
          <TabsTrigger value="bandwidth">Bandwidth</TabsTrigger>
        </TabsList>

        {/* Keys: the keys list + inline governance editor (the four states live here). */}
        <TabsContent value="keys">
          {isLoading && (
            <Loading
              label="Loading API keys"
              data-testid="loading"
              className="animate-pulse"
            />
          )}

          {isError && !isLoading && <ErrorState title={getErrorTitle(error)} />}

          {!isLoading && !isError && keys !== undefined && keys.length === 0 && (
            <Empty
              title="No API keys yet"
              description="Create your first key to get started."
            />
          )}

          {!isLoading && !isError && keys !== undefined && keys.length > 0 && (
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
                    {keys.map((key) => (
                      <Fragment key={key.key_id}>
                        <KeyRow
                          apiKey={key}
                          onRevoke={handleRevoke}
                          isPendingRevoke={revokeTargetId === key.key_id}
                        />
                        {/* Governance editor — its own sibling row (KeyRow renders a <tr>) */}
                        <TableRow>
                          <TableCell colSpan={6}>
                            <Button
                              type="button"
                              variant="ghost"
                              onClick={() =>
                                setExpandedGovernance(
                                  expandedGovernance === key.key_id ? null : key.key_id
                                )
                              }
                            >
                              {expandedGovernance === key.key_id
                                ? "Hide governance"
                                : "Governance"}
                            </Button>
                            {expandedGovernance === key.key_id && (
                              <KeyGovernanceEditor
                                apiKey={toGovernanceKey(key)}
                                onUpdated={handleGovernanceUpdated}
                              />
                            )}
                          </TableCell>
                        </TableRow>
                      </Fragment>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Rate limits: read-only live rate-limit usage per key (rpm/tpm vs configured). */}
        <TabsContent value="rate-limits">
          <RatelimitsPanel />
        </TabsContent>

        {/* Bandwidth: read-only live bandwidth bucket level per key (current vs capacity). */}
        <TabsContent value="bandwidth">
          <BandwidthPanel />
        </TabsContent>
      </Tabs>

      <CreateKeyDialog
        isOpen={isCreateDialogOpen}
        onClose={() => setIsCreateDialogOpen(false)}
        onSubmit={handleCreateKey}
        onTierChange={setSelectedTier}
      />
    </div>
  );
}
