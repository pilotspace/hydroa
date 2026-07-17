"use client";

/**
 * ModelCatalogTable — renders the model catalog (data supplied by the caller,
 * which reads GET /admin/catalog/models — the JWT/control-plane twin of /v1/models)
 * IMPORTANT: uses div-based rows (NOT <table>/<tr>) so that test assertions
 * on queryAllByRole("row") only count usage record rows, not catalog rows.
 * States: loading (spinner), empty, error (problem+json title), success
 */

import { Badge } from "@/components/ui";
import { BffError } from "@/lib/bff-client";

export interface ModelEntry {
  id: string;
  name: string;
  context_length: number | null;
  prompt_per_token: number;
  completion_per_token: number;
  object: string;
  input_modalities?: string[];
}

export interface ModelsData {
  object: string;
  data: ModelEntry[];
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

interface ModelCatalogTableProps {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  data: ModelsData | undefined;
}

export function ModelCatalogTable({
  isLoading,
  isError,
  error,
  data,
}: ModelCatalogTableProps) {
  if (isLoading) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label="Loading model catalog"
        className="animate-pulse"
      >
        <span>Loading catalog…</span>
      </div>
    );
  }

  if (isError) {
    return <p role="alert">{getErrorTitle(error)}</p>;
  }

  if (!data || data.data.length === 0) {
    return <p>No models available</p>;
  }

  return (
    <div>
      {/* Header row */}
      <div className="grid grid-cols-5 gap-2 font-semibold border-b py-2">
        <span>Name</span>
        <span>Context Length</span>
        <span>Prompt/token</span>
        <span>Completion/token</span>
        <span>Inputs</span>
      </div>
      {/*
        Data rows — div-based (no <tr>) so the usage-records table owns the
        "row" role. The model ID is rendered as visible secondary text with an
        "ID: " prefix: users need the exact ID for API calls, and the prefixed
        node text stays distinct from the bare ID shown in usage-record cells.
      */}
      {data.data.map((model) => (
        <div
          key={model.id}
          data-model-id={model.id}
          className="grid grid-cols-5 gap-2 border-b py-2"
        >
          <span>
            {model.name}
            {/* sr-only bare ID: the visible ID node reads "ID: <id>", so an exact
                getByText(model.id) would not match it. This hidden node carries the
                bare ID as its own exact-match target; the visible "ID: …" span is
                aria-hidden so the ID is announced once, not twice. */}
            <span className="sr-only">{model.id}</span>
            <span
              className="block text-xs text-muted-foreground"
              aria-hidden="true"
            >
              ID: {model.id}
            </span>
          </span>
          <span>{model.context_length ?? "—"}</span>
          <span>{model.prompt_per_token}</span>
          <span>{model.completion_per_token}</span>
          <span className="flex flex-wrap gap-1">
            {(model.input_modalities ?? []).map((m) => (
              <Badge key={m} variant="secondary">
                {m}
              </Badge>
            ))}
          </span>
        </div>
      ))}
    </div>
  );
}
