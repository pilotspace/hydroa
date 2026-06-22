"use client";

/**
 * ModelsPage — owner/admin model-management surface (`/models`).
 *
 * Lists the tenant's catalog models and toggles each enabled/disabled per tenant,
 * consuming the EXISTING gateway endpoints through the BFF seam:
 *   GET /admin/models            -> { object:"list", data: AdminModelItem[] }
 *   PUT /admin/models/{id:path}  body { enabled } -> AdminModelItem
 *
 * Owner/admin-only on the backend (member GET 403s) — surfaced as the standard
 * ErrorState (role=alert) carrying the BFF error title, never a fabricated list.
 *
 * Data calls use bff-client.ts (credentials:"include") — no Authorization header
 * is ever constructed client-side. Field names are byte-identical to the gateway.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, bffPost, bffPut, BffError } from "@/lib/bff-client";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import {
  Button,
  Card,
  CardContent,
  DataTable,
  Switch,
  Loading,
  ErrorState,
  Empty,
} from "@/components/ui";

/** A catalog model with the caller-tenant's enabled override (gateway AdminModelItem). */
interface AdminModelItem {
  id: string;
  name: string;
  context_length: number | null;
  enabled: boolean;
}

interface AdminModelsListResponse {
  object: string;
  data: AdminModelItem[];
}

/** Response of POST /admin/catalog/sync (gateway CatalogSyncResponse). */
interface CatalogSyncResponse {
  synced: number;
  synced_at: string;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function ModelsPage() {
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);

  // Role from /api/auth/me — owner/admin may force a catalog re-sync; members may not.
  const { data: currentUser } = useCurrentUser();
  const canManage = currentUser?.role === "owner" || currentUser?.role === "admin";

  const { data, isLoading, isError, error } = useQuery<AdminModelsListResponse>({
    queryKey: ["admin-models"],
    queryFn: () => bffGet<AdminModelsListResponse>("/admin/models"),
  });

  const resyncCatalog = useMutation({
    mutationFn: () => bffPost<CatalogSyncResponse>("/admin/catalog/sync", {}),
    onSuccess: (result) => {
      setLastSync(result.synced_at);
      void queryClient.invalidateQueries({ queryKey: ["admin-models"] });
    },
  });

  const toggleModel = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      bffPut<AdminModelItem>(`/admin/models/${encodeURIComponent(id)}`, { enabled }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-models"] });
    },
    onSettled: () => {
      setPendingId(null);
    },
  });

  function handleToggle(id: string, next: boolean) {
    setPendingId(id);
    toggleModel.mutate({ id, enabled: next });
  }

  const models = data?.data ?? [];

  // Columns defined in-component so the Enabled cell closes over the toggle mutation +
  // pendingId. Non-sortable (enableSorting:false) — behavior-preserving vs the prior <Table>.
  const columns: ColumnDef<AdminModelItem>[] = [
    {
      accessorKey: "name",
      header: "Model",
      enableSorting: false,
      cell: ({ row }) => (
        <>
          <div className="font-medium text-foreground">{row.original.name}</div>
          <div className="text-xs text-muted-foreground">{row.original.id}</div>
        </>
      ),
    },
    {
      accessorKey: "context_length",
      header: "Context length",
      enableSorting: false,
      cell: ({ row }) => (
        <span className="text-muted-foreground">
          {row.original.context_length !== null
            ? row.original.context_length.toLocaleString()
            : "—"}
        </span>
      ),
    },
    {
      id: "enabled",
      header: "Enabled",
      enableSorting: false,
      cell: ({ row }) => (
        <Switch
          checked={row.original.enabled}
          aria-label={`Enable ${row.original.name}`}
          disabled={toggleModel.isPending && pendingId === row.original.id}
          onCheckedChange={(next) => handleToggle(row.original.id, next)}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Models</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Enable or disable individual catalog models for your tenant.
          </p>
        </div>
        {canManage && (
          <div className="flex flex-col items-end gap-1">
            <Button
              type="button"
              onClick={() => resyncCatalog.mutate()}
              disabled={resyncCatalog.isPending}
            >
              {resyncCatalog.isPending ? "Re-syncing…" : "Re-sync catalog"}
            </Button>
            {lastSync && (
              <span className="text-xs text-muted-foreground">Last synced: {lastSync}</span>
            )}
          </div>
        )}
      </header>

      {/* A failed catalog re-sync (e.g. 502 ERR_UPSTREAM_UNAVAILABLE) surfaces inline. */}
      {resyncCatalog.isError && <ErrorState title={getErrorTitle(resyncCatalog.error)} />}

      {isLoading && <Loading label="Loading models" className="animate-pulse" />}

      {isError && !isLoading && <ErrorState title={getErrorTitle(error)} />}

      {/* A failed toggle (e.g. PUT 404 ERR_MODEL_NOT_FOUND) surfaces inline — never silent;
          the list stays visible and the server state is unchanged. */}
      {toggleModel.isError && (
        <ErrorState title={getErrorTitle(toggleModel.error)} />
      )}

      {!isLoading && !isError && models.length === 0 && (
        <Empty
          title="No models available"
          description="Your catalog has no active models yet."
        />
      )}

      {!isLoading && !isError && models.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <DataTable columns={columns} data={models} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
