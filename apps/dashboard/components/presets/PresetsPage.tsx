"use client";

/**
 * PresetsPage — authenticated model-preset management page
 *
 * Auth guard: middleware.ts handles cookie-presence check server-side.
 * If this page renders, the session cookie is present.
 *
 * Mirrors components/keys/KeysPage.tsx's data-flow (useQuery/useMutation +
 * invalidateQueries) and Table-rendering shape, adapted for the presets
 * domain: a create form (preset_name/alias_key/target_model) + a table of
 * the tenant's existing presets, per preset-admin-surface TASK.md §3
 * CONTRACT (FROZEN @ v2).
 *
 * States: loading (spinner), empty, error (problem+json title), success (table)
 * Actions: create preset (inline form, no dialog — §3 CONTRACT declares no
 *          separate dialog file for this surface), delete preset (immediate,
 *          idempotent server-side — no confirm step, per §2 SCENARIOS
 *          "dashboard page renders and manages presets end to end")
 *
 * All data calls use bff-client.ts (credentials:"include") — no Authorization
 * header is ever constructed or read client-side.
 *
 * PUT/DELETE address a row by PATH (`/admin/presets/{preset_name}/{alias_key}`),
 * NOT a JSON body carrying preset_name/alias_key — that was the rejected v1
 * draft (TASK.md §3 Status). PUT body is only `{target_model}`; DELETE has no
 * body at all.
 */

import { useState, FormEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, bffDelete, BffError } from "@/lib/bff-client";
import { PresetRow } from "./PresetRow";
import {
  Button,
  Card,
  CardContent,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  Input,
  Loading,
  ErrorState,
  Empty,
} from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";

interface Preset {
  preset_name: string;
  alias_key: string;
  target_model: string;
  updated_at: string;
}

interface PresetsResponse {
  presets: Preset[];
}

/**
 * Client-side mirror of the server's router-local "/" guard (TASK.md §3
 * CONTRACT `_reject_slash`) — a cheap UX win that avoids a round-trip for a
 * selector the server will reject anyway. The server remains the enforced
 * source of truth; this is presentation-layer only.
 */
function containsSlash(value: string): boolean {
  return value.includes("/");
}

export function PresetsPage() {
  const queryClient = useQueryClient();
  const [presetName, setPresetName] = useState("");
  const [aliasKey, setAliasKey] = useState("");
  const [targetModel, setTargetModel] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  // Presets query — uses bffGet (credentials:"include") through the catch-all BFF proxy
  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery<PresetsResponse>({
    queryKey: ["admin-presets"],
    queryFn: () => bffGet<PresetsResponse>("/admin/presets"),
  });

  const presets = data?.presets;

  // Upsert (create) mutation — path-templated URL, body is only {target_model}
  const upsertMutation = useMutation({
    mutationFn: ({
      preset_name,
      alias_key,
      target_model,
    }: {
      preset_name: string;
      alias_key: string;
      target_model: string;
    }) =>
      bffPut<Preset>(
        `/admin/presets/${encodeURIComponent(preset_name)}/${encodeURIComponent(alias_key)}`,
        { target_model }
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-presets"] });
    },
  });

  // Delete mutation — path-templated URL, no body, always 204 (idempotent)
  const deleteMutation = useMutation({
    mutationFn: ({
      preset_name,
      alias_key,
    }: {
      preset_name: string;
      alias_key: string;
    }) =>
      bffDelete(
        `/admin/presets/${encodeURIComponent(preset_name)}/${encodeURIComponent(alias_key)}`
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-presets"] });
    },
  });

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    const trimmedName = presetName.trim();
    const trimmedAlias = aliasKey.trim();
    const trimmedTarget = targetModel.trim();

    // Required-field check — mirrors CreateKeyDialog's own required-field UX
    // (empty rejected before any network call), just inline rather than in a
    // separate dialog (this surface has no dialog file per §3 CONTRACT).
    if (!trimmedName || !trimmedAlias || !trimmedTarget) {
      setFormError("Preset name, alias key, and target model are all required.");
      return;
    }

    // Client-side "/" guard — mirrors the server-side router-local guard
    // (TASK.md §1 ⚠, §3 CONTRACT _reject_slash) before ever calling bffPut.
    if (containsSlash(trimmedName) || containsSlash(trimmedAlias)) {
      setFormError('Preset name and alias key cannot contain "/".');
      return;
    }

    try {
      await upsertMutation.mutateAsync({
        preset_name: trimmedName,
        alias_key: trimmedAlias,
        target_model: trimmedTarget,
      });
      setPresetName("");
      setAliasKey("");
      setTargetModel("");
    } catch (err) {
      if (err instanceof BffError) {
        setFormError(err.problem.title ?? "Failed to save preset");
      } else {
        setFormError("An unexpected error occurred");
      }
    }
  }

  function handleDelete(presetNameToDelete: string, aliasKeyToDelete: string) {
    deleteMutation.mutate({
      preset_name: presetNameToDelete,
      alias_key: aliasKeyToDelete,
    });
  }

  // Get error title from BFF error
  function getErrorTitle(err: unknown): string {
    if (err instanceof BffError) return err.problem.title;
    if (err instanceof Error) return err.message;
    return "An error occurred";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Model Presets"
        description="Create named (preset, alias) shortcuts that resolve requests to a concrete catalog model."
      />

      <Card>
        <CardContent className="flex flex-col gap-4 p-6">
          <form
            onSubmit={handleSubmit}
            noValidate
            className="flex flex-col gap-4 sm:flex-row sm:items-end sm:flex-wrap"
          >
            <div className="flex flex-col gap-1">
              <label
                htmlFor="preset_name_input"
                className="text-sm font-medium text-foreground"
              >
                Preset name
              </label>
              <Input
                id="preset_name_input"
                type="text"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label
                htmlFor="alias_key_input"
                className="text-sm font-medium text-foreground"
              >
                Alias key
              </label>
              <Input
                id="alias_key_input"
                type="text"
                value={aliasKey}
                onChange={(e) => setAliasKey(e.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label
                htmlFor="target_model_input"
                className="text-sm font-medium text-foreground"
              >
                Target model
              </label>
              <Input
                id="target_model_input"
                type="text"
                value={targetModel}
                onChange={(e) => setTargetModel(e.target.value)}
                autoComplete="off"
              />
            </div>
            <Button type="submit" disabled={upsertMutation.isPending}>
              {upsertMutation.isPending ? "Saving…" : "Add preset"}
            </Button>
          </form>
          {formError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {formError}
            </p>
          )}
        </CardContent>
      </Card>

      {isLoading && (
        <Loading
          label="Loading presets"
          data-testid="loading"
          className="animate-pulse"
        />
      )}

      {isError && !isLoading && <ErrorState title={getErrorTitle(error)} />}

      {!isLoading && !isError && presets !== undefined && presets.length === 0 && (
        <Empty
          title="No presets yet"
          description="Create your first preset to get started."
        />
      )}

      {!isLoading && !isError && presets !== undefined && presets.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Preset name</TableHead>
                  <TableHead>Alias key</TableHead>
                  <TableHead>Target model</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {presets.map((preset) => (
                  <PresetRow
                    key={`${preset.preset_name}:${preset.alias_key}`}
                    preset={preset}
                    onDelete={handleDelete}
                    isPendingDelete={
                      deleteMutation.isPending &&
                      deleteMutation.variables?.preset_name === preset.preset_name &&
                      deleteMutation.variables?.alias_key === preset.alias_key
                    }
                  />
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
