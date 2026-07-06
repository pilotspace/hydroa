"use client";

/**
 * PlatformConfigTab — Config tab (M6) of the tenant-detail screen: two stacked,
 * independently-loading/saving Cards (Cache, Guardrails), parametrized by the
 * TARGET tenant_id rather than the caller's own tenant.
 *
 * GET/PUT .../{tenantId}/cache -> CacheGetResponse/CachePutRequest {enabled,
 * semantic_enabled} and GET/PUT .../{tenantId}/guardrails -> GuardrailConfig
 * {prompt_injection, pii_mask} are byte-identical to the self-service
 * CacheSettings.tsx/GuardrailSettings.tsx DTOs (platform_tenant_config_router.py
 * imports the SAME schema classes — TASK.md §0 GROUND, confirmed by reading its
 * imports directly). Each card mirrors its self-service counterpart field-for-
 * field, EXCEPT the Guardrails card's Save button is labeled "Save guardrails"
 * (not a bare "Save") — a deliberate, minimal divergence so the two cards' save
 * actions have distinct accessible names on the SAME tab (R4 isolation: each
 * card's own mutation/error state must be independently addressable).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Switch, Button, Input, Loading, ErrorState, Success, Card, CardHeader, CardTitle, CardContent } from "@/components/ui";

interface CacheConfig {
  enabled: boolean;
  semantic_enabled: boolean;
}

interface PIConf {
  enabled: boolean;
  mode: "block" | "audit";
}

interface PatternRow {
  name: string;
  pattern: string;
}

interface PiiConf {
  enabled: boolean;
  mode: "mask" | "audit";
  pii_custom_patterns?: PatternRow[];
}

interface GuardrailConfig {
  prompt_injection: PIConf | null;
  pii_mask: PiiConf | null;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

function PlatformCacheCard({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const queryKey = ["platform-tenant-cache", tenantId];

  const { data, isLoading, isError, error, refetch } = useQuery<CacheConfig>({
    queryKey,
    queryFn: () => bffGet<CacheConfig>(`/admin/platform/tenants/${tenantId}/cache`),
    retry: false,
  });

  const [enabled, setEnabled] = useState(false);
  const [semanticEnabled, setSemanticEnabled] = useState(false);
  const [mutError, setMutError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const onToggleEnabled = (v: boolean) => {
    setEnabled(v);
    setSaved(false);
  };
  const onToggleSemantic = (v: boolean) => {
    setSemanticEnabled(v);
    setSaved(false);
  };

  // Seed/reseed local editable state from server data via React's "adjust state
  // during render" guard (no setState in an effect) — fires once per new data
  // reference (initial load + post-save reinstall via setQueryData).
  const [seededData, setSeededData] = useState<CacheConfig | undefined>(undefined);
  if (data && data !== seededData) {
    setSeededData(data);
    setEnabled(data.enabled);
    setSemanticEnabled(data.semantic_enabled);
  }

  const saveCache = useMutation({
    mutationFn: (body: CacheConfig) =>
      bffPut<CacheConfig>(`/admin/platform/tenants/${tenantId}/cache`, body),
    onSuccess: (resp) => {
      setMutError(null);
      setSaved(true);
      queryClient.setQueryData<CacheConfig>(queryKey, resp);
      setEnabled(resp.enabled);
      setSemanticEnabled(resp.semantic_enabled);
    },
    onError: (err) => {
      setMutError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  if (isLoading) {
    return <Loading label="Loading cache settings" className="animate-pulse" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />;
  }

  return (
    <Card variant="flat">
      <CardHeader>
        <CardTitle>Cache</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-4">
            <label htmlFor="platform-cache-enabled" className="text-sm font-medium text-foreground">
              Response cache
            </label>
            <Switch
              id="platform-cache-enabled"
              aria-label="Enable response cache"
              checked={enabled}
              onCheckedChange={onToggleEnabled}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="platform-semantic-cache-enabled" className="text-sm font-medium text-foreground">
              Semantic cache
            </label>
            <Switch
              id="platform-semantic-cache-enabled"
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
            className="rounded-[var(--radius-flat-control)]"
            disabled={saveCache.isPending}
            onClick={() => saveCache.mutate({ enabled, semantic_enabled: semanticEnabled })}
          >
            {saveCache.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function PlatformGuardrailsCard({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const queryKey = ["platform-tenant-guardrails", tenantId];

  const { data, isLoading, isError, error, refetch } = useQuery<GuardrailConfig>({
    queryKey,
    queryFn: () => bffGet<GuardrailConfig>(`/admin/platform/tenants/${tenantId}/guardrails`),
    retry: false,
  });

  const [piEnabled, setPiEnabled] = useState(false);
  const [piMode, setPiMode] = useState<"block" | "audit">("block");
  const [piiEnabled, setPiiEnabled] = useState(false);
  const [piiMode, setPiiMode] = useState<"mask" | "audit">("mask");
  const [patterns, setPatterns] = useState<PatternRow[]>([]);
  const [mutError, setMutError] = useState<string | null>(null);

  const [seededData, setSeededData] = useState<GuardrailConfig | undefined>(undefined);
  if (data && data !== seededData) {
    setSeededData(data);
    const pi = data.prompt_injection;
    setPiEnabled(pi?.enabled ?? false);
    setPiMode(pi?.mode ?? "block");

    const pii = data.pii_mask;
    setPiiEnabled(pii?.enabled ?? false);
    setPiiMode(pii?.mode ?? "mask");
    setPatterns(pii?.pii_custom_patterns ?? []);
  }

  const saveGuardrails = useMutation({
    mutationFn: (body: GuardrailConfig) =>
      bffPut<GuardrailConfig>(`/admin/platform/tenants/${tenantId}/guardrails`, body),
    onSuccess: (resp) => {
      setMutError(null);
      queryClient.setQueryData<GuardrailConfig>(queryKey, resp);
    },
    onError: (err) => {
      setMutError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  function handleAddPattern() {
    if (patterns.length < 8) {
      setPatterns((prev) => [...prev, { name: "", pattern: "" }]);
    }
  }
  function handleRemovePattern(idx: number) {
    setPatterns((prev) => prev.filter((_, i) => i !== idx));
  }
  function handlePatternChange(idx: number, field: keyof PatternRow, value: string) {
    setPatterns((prev) => prev.map((row, i) => (i === idx ? { ...row, [field]: value } : row)));
  }
  function handleSave() {
    setMutError(null);
    saveGuardrails.mutate({
      prompt_injection: { enabled: piEnabled, mode: piMode },
      pii_mask: { enabled: piiEnabled, mode: piiMode, pii_custom_patterns: patterns },
    });
  }

  if (isLoading) {
    return <Loading label="Loading guardrail settings" className="animate-pulse" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />;
  }

  return (
    <Card variant="flat">
      <CardHeader>
        <CardTitle>Guardrails</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
          <legend className="px-1 text-sm font-semibold text-foreground">Prompt injection</legend>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="platform-pi-enabled" className="text-sm font-medium text-foreground">
              Enable prompt injection protection
            </label>
            <Switch
              id="platform-pi-enabled"
              aria-label="Enable prompt injection protection"
              checked={piEnabled}
              onCheckedChange={setPiEnabled}
            />
          </div>

          <div className="flex items-center gap-3">
            <label htmlFor="platform-pi-mode" className="text-sm font-medium text-foreground">
              Mode
            </label>
            <select
              id="platform-pi-mode"
              aria-label="Prompt injection mode"
              value={piMode}
              onChange={(e) => setPiMode(e.target.value as "block" | "audit")}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="block">Block</option>
              <option value="audit">Audit</option>
            </select>
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
          <legend className="px-1 text-sm font-semibold text-foreground">PII masking</legend>

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="platform-pii-enabled" className="text-sm font-medium text-foreground">
              Enable PII masking
            </label>
            <Switch
              id="platform-pii-enabled"
              aria-label="Enable PII masking"
              checked={piiEnabled}
              onCheckedChange={setPiiEnabled}
            />
          </div>

          <div className="flex items-center gap-3">
            <label htmlFor="platform-pii-mode" className="text-sm font-medium text-foreground">
              Mode
            </label>
            <select
              id="platform-pii-mode"
              aria-label="PII masking mode"
              value={piiMode}
              onChange={(e) => setPiiMode(e.target.value as "mask" | "audit")}
              className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="mask">Mask</option>
              <option value="audit">Audit</option>
            </select>
          </div>

          <div className="flex flex-col gap-2">
            {patterns.map((row, idx) => {
              const n = idx + 1;
              return (
                <div key={idx} className="flex items-center gap-2">
                  <Input
                    aria-label={`Pattern name ${n}`}
                    placeholder="Name"
                    value={row.name}
                    onChange={(e) => handlePatternChange(idx, "name", e.target.value)}
                    className="flex-1 rounded-[var(--radius-flat-control)]"
                  />
                  <Input
                    aria-label={`Pattern regex ${n}`}
                    placeholder="Regex"
                    value={row.pattern}
                    onChange={(e) => handlePatternChange(idx, "pattern", e.target.value)}
                    className="flex-1 rounded-[var(--radius-flat-control)]"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="rounded-[var(--radius-flat-control)]"
                    aria-label={`Remove pattern ${n}`}
                    onClick={() => handleRemovePattern(idx)}
                  >
                    Remove
                  </Button>
                </div>
              );
            })}
          </div>

          {patterns.length < 8 && (
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="rounded-[var(--radius-flat-control)]"
                onClick={handleAddPattern}
              >
                Add pattern
              </Button>
            </div>
          )}
        </fieldset>

        {mutError && (
          <p role="alert" aria-live="polite" className="text-sm text-destructive">
            {mutError}
          </p>
        )}

        <div>
          {/* Deliberately NOT a bare "Save" — see file header: disambiguates this
              card's own save action from the Cache card's "Save" on the same tab. */}
          <Button
            type="button"
            className="rounded-[var(--radius-flat-control)]"
            disabled={saveGuardrails.isPending}
            onClick={handleSave}
          >
            {saveGuardrails.isPending ? "Saving…" : "Save guardrails"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export interface PlatformConfigTabProps {
  tenantId: string;
}

export function PlatformConfigTab({ tenantId }: PlatformConfigTabProps) {
  return (
    <div className="flex flex-col gap-6">
      <PlatformCacheCard tenantId={tenantId} />
      <PlatformGuardrailsCard tenantId={tenantId} />
    </div>
  );
}
