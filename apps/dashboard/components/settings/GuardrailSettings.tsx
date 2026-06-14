"use client";

/**
 * GuardrailSettings — Guardrails tab content for the /settings hub.
 *
 * Consumes GET /admin/guardrails → { prompt_injection: PIConf|null, pii_mask: PiiConf|null }.
 * PUT /admin/guardrails on Save.
 * A null block defaults to { enabled: false, mode: first-option }.
 * Custom patterns: add/remove rows (≤8), each row has name+regex inputs.
 * BffError title surfaced inline (anti-silent-failure standing rule).
 */

import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Switch, Button, Input, Loading, ErrorState } from "@/components/ui";

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

export function GuardrailSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<GuardrailConfig>({
    queryKey: ["admin-guardrails"],
    queryFn: () => bffGet<GuardrailConfig>("/admin/guardrails"),
    // design-for-failure: a 403/404 is deterministic — don't retry-storm a settled error
    retry: false,
  });

  // Prompt injection state
  const [piEnabled, setPiEnabled] = useState(false);
  const [piMode, setPiMode] = useState<"block" | "audit">("block");

  // PII mask state
  const [piiEnabled, setPiiEnabled] = useState(false);
  const [piiMode, setPiiMode] = useState<"mask" | "audit">("mask");
  const [patterns, setPatterns] = useState<PatternRow[]>([]);

  const [mutError, setMutError] = useState<string | null>(null);

  useEffect(() => {
    if (data) {
      const pi = data.prompt_injection;
      setPiEnabled(pi?.enabled ?? false);
      setPiMode(pi?.mode ?? "block");

      const pii = data.pii_mask;
      setPiiEnabled(pii?.enabled ?? false);
      setPiiMode(pii?.mode ?? "mask");
      setPatterns(pii?.pii_custom_patterns ?? []);
    }
  }, [data]);

  const saveGuardrails = useMutation({
    mutationFn: (body: GuardrailConfig) =>
      bffPut<GuardrailConfig>("/admin/guardrails", body),
    onSuccess: (resp) => {
      setMutError(null);
      queryClient.setQueryData<GuardrailConfig>(["admin-guardrails"], resp);
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
    setPatterns((prev) =>
      prev.map((row, i) => (i === idx ? { ...row, [field]: value } : row)),
    );
  }

  function handleSave() {
    setMutError(null);
    saveGuardrails.mutate({
      prompt_injection: { enabled: piEnabled, mode: piMode },
      pii_mask: {
        enabled: piiEnabled,
        mode: piiMode,
        pii_custom_patterns: patterns,
      },
    });
  }

  if (isLoading) {
    return <Loading label="Loading guardrail settings" />;
  }

  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Prompt Injection */}
      <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
        <legend className="px-1 text-sm font-semibold text-foreground">Prompt injection</legend>

        <div className="flex items-center justify-between gap-4">
          <label htmlFor="pi-enabled" className="text-sm font-medium text-foreground">
            Enable prompt injection protection
          </label>
          <Switch
            id="pi-enabled"
            aria-label="Enable prompt injection protection"
            checked={piEnabled}
            onCheckedChange={setPiEnabled}
          />
        </div>

        <div className="flex items-center gap-3">
          <label htmlFor="pi-mode" className="text-sm font-medium text-foreground">
            Mode
          </label>
          <select
            id="pi-mode"
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

      {/* PII Masking */}
      <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
        <legend className="px-1 text-sm font-semibold text-foreground">PII masking</legend>

        <div className="flex items-center justify-between gap-4">
          <label htmlFor="pii-enabled" className="text-sm font-medium text-foreground">
            Enable PII masking
          </label>
          <Switch
            id="pii-enabled"
            aria-label="Enable PII masking"
            checked={piiEnabled}
            onCheckedChange={setPiiEnabled}
          />
        </div>

        <div className="flex items-center gap-3">
          <label htmlFor="pii-mode" className="text-sm font-medium text-foreground">
            Mode
          </label>
          <select
            id="pii-mode"
            aria-label="PII masking mode"
            value={piiMode}
            onChange={(e) => setPiiMode(e.target.value as "mask" | "audit")}
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="mask">Mask</option>
            <option value="audit">Audit</option>
          </select>
        </div>

        {/* Custom patterns */}
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
                  className="flex-1"
                />
                <Input
                  aria-label={`Pattern regex ${n}`}
                  placeholder="Regex"
                  value={row.pattern}
                  onChange={(e) => handlePatternChange(idx, "pattern", e.target.value)}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
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
        <Button
          type="button"
          disabled={saveGuardrails.isPending}
          onClick={handleSave}
        >
          {saveGuardrails.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
