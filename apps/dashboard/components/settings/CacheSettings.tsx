"use client";

/**
 * CacheSettings — Cache tab content for the /settings hub.
 *
 * Consumes GET /admin/cache → { enabled, semantic_enabled }.
 * PUT /admin/cache { enabled, semantic_enabled } on Save.
 * BffError title surfaced inline (anti-silent-failure standing rule).
 * client_secret is irrelevant here; this tab works for any role (owner/admin);
 * a member's PUT → 403 surfaces inline.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Switch, Button, Loading, ErrorState, Success } from "@/components/ui";

interface CacheConfig {
  enabled: boolean;
  semantic_enabled: boolean;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function CacheSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<CacheConfig>({
    queryKey: ["admin-cache"],
    queryFn: () => bffGet<CacheConfig>("/admin/cache"),
    // design-for-failure: a 403/404 is deterministic — don't retry-storm a settled error
    retry: false,
  });

  const [enabled, setEnabled] = useState(false);
  const [semanticEnabled, setSemanticEnabled] = useState(false);
  const [mutError, setMutError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Editing after a save clears the confirmation so it never lingers over unsaved changes.
  const onToggleEnabled = (v: boolean) => {
    setEnabled(v);
    setSaved(false);
  };
  const onToggleSemantic = (v: boolean) => {
    setSemanticEnabled(v);
    setSaved(false);
  };

  // Seed/reseed local editable state from server data via React's "adjust state
  // during render" guard (no setState in an effect): fires only when the query
  // data reference changes (initial load + after a save reinstalls it via
  // setQueryData), so it converges in one render and never loops.
  const [seededData, setSeededData] = useState<CacheConfig | undefined>(undefined);
  if (data && data !== seededData) {
    setSeededData(data);
    setEnabled(data.enabled);
    setSemanticEnabled(data.semantic_enabled);
  }

  const saveCache = useMutation({
    mutationFn: (body: CacheConfig) => bffPut<CacheConfig>("/admin/cache", body),
    onSuccess: (resp) => {
      setMutError(null);
      setSaved(true);
      queryClient.setQueryData<CacheConfig>(["admin-cache"], resp);
      setEnabled(resp.enabled);
      setSemanticEnabled(resp.semantic_enabled);
    },
    onError: (err) => {
      setMutError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  if (isLoading) {
    return <Loading label="Loading cache settings" />;
  }

  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4">
          <label htmlFor="cache-enabled" className="text-sm font-medium text-foreground">
            Response cache
          </label>
          <Switch
            id="cache-enabled"
            aria-label="Enable response cache"
            checked={enabled}
            onCheckedChange={onToggleEnabled}
          />
        </div>

        <div className="flex items-center justify-between gap-4">
          <label htmlFor="semantic-cache-enabled" className="text-sm font-medium text-foreground">
            Semantic cache
          </label>
          <Switch
            id="semantic-cache-enabled"
            aria-label="Enable semantic cache"
            checked={semanticEnabled}
            onCheckedChange={onToggleSemantic}
          />
        </div>
      </div>

      {mutError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {mutError}
        </p>
      )}

      {saved && !mutError && <Success title="Saved." />}

      <div>
        <Button
          type="button"
          disabled={saveCache.isPending}
          onClick={() => saveCache.mutate({ enabled, semantic_enabled: semanticEnabled })}
        >
          {saveCache.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
