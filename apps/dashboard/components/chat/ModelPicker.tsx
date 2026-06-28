"use client";

/**
 * components/chat/ModelPicker.tsx — v40 chat-model-controls.
 *
 * Catalog-model picker for the chat composer. Fetches GET /api/gw/admin/catalog/models
 * (the JWT/control-plane twin of /v1/models — a session cookie can't reach /v1/* through
 * the edge ext_authz, which would 401 and log the user out) once on mount, cookie-only
 * via bff-client — a self-contained fetch matching useChatStream's style (no
 * QueryClientProvider needed in isolation renders).
 *
 * FAILS OPEN by design: while loading, on a fetch error, or on an empty list it
 * still renders the current `value` as a selectable option, so the user is never
 * blocked from sending and no fabricated model list is shown. The gateway remains
 * the source of truth for what a key may actually call.
 */

import { useEffect, useState } from "react";
import { bffGet } from "@/lib/bff-client";
import { cn } from "@/lib/cn";

interface ModelEntry {
  id: string;
}
interface ModelsData {
  object: string;
  data: ModelEntry[];
}

export interface ModelPickerProps {
  value: string;
  onChange: (id: string) => void;
  className?: string;
}

export function ModelPicker({ value, onChange, className }: ModelPickerProps) {
  const [models, setModels] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    bffGet<ModelsData>("/admin/catalog/models")
      .then((res) => {
        if (active) setModels(res.data?.map((m) => m.id) ?? []);
      })
      .catch(() => {
        // fail-open: keep only the current value selectable; surface nothing
        if (active) setModels([]);
      });
    return () => {
      active = false;
    };
  }, []);

  // Always include the current value so the picker can never block sending.
  const options = models.includes(value) ? models : [value, ...models];

  return (
    <select
      aria-label="Model"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "rounded-md border border-border bg-background px-2 py-1 text-xs font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      {options.map((id) => (
        <option key={id} value={id}>
          {id}
        </option>
      ))}
    </select>
  );
}
