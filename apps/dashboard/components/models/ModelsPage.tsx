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
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import {
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
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

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function ModelsPage() {
  const queryClient = useQueryClient();
  const [pendingId, setPendingId] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery<AdminModelsListResponse>({
    queryKey: ["admin-models"],
    queryFn: () => bffGet<AdminModelsListResponse>("/admin/models"),
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

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Models</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Enable or disable individual catalog models for your tenant.
        </p>
      </header>

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
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead>Context length</TableHead>
                  <TableHead>Enabled</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {models.map((model) => (
                  <TableRow key={model.id}>
                    <TableCell>
                      <div className="font-medium text-foreground">{model.name}</div>
                      <div className="text-xs text-muted-foreground">{model.id}</div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {model.context_length !== null
                        ? model.context_length.toLocaleString()
                        : "—"}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={model.enabled}
                        aria-label={`Enable ${model.name}`}
                        disabled={toggleModel.isPending && pendingId === model.id}
                        onCheckedChange={(next) => handleToggle(model.id, next)}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
