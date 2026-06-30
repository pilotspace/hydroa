"use client";

/**
 * components/chat/ModelControls.tsx — the relocated per-request controls.
 *
 * v40 shipped these behind a composer "Model settings" disclosure. The
 * chat-playground-shell reshape MOVES them into the inspector's Parameters tab,
 * where they are shown directly (the panel is the container; no nested
 * disclosure). The control set, state wiring, and aria-labels are UNCHANGED —
 * System prompt / Temperature / Web search — so the chat-model-controls and
 * chat-websearch suites reach them by navigating to the Parameters tab. Pure
 * presentation: state is lifted to ChatWorkspace, which threads it into send().
 */

import { Textarea } from "@/components/ui/textarea";

export interface ModelControlsProps {
  system: string;
  onSystemChange: (s: string) => void;
  temperature: number;
  onTemperatureChange: (t: number) => void;
  webSearch: boolean;
  onWebSearchChange: (v: boolean) => void;
}

export function ModelControls({
  system,
  onSystemChange,
  temperature,
  onTemperatureChange,
  webSearch,
  onWebSearchChange,
}: ModelControlsProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="chat-system" className="text-xs font-medium text-foreground">
          System prompt
        </label>
        <Textarea
          id="chat-system"
          aria-label="System prompt"
          rows={3}
          value={system}
          onChange={(e) => onSystemChange(e.target.value)}
          placeholder="Optional — steer the assistant's behavior"
          className="resize-none text-sm"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor="chat-temp" className="text-xs font-medium text-foreground">
            Temperature
          </label>
          <span className="w-10 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 text-right text-xs tabular-nums text-foreground">
            {temperature.toFixed(1)}
          </span>
        </div>
        <input
          id="chat-temp"
          aria-label="Temperature"
          type="range"
          min={0}
          max={2}
          step={0.1}
          value={temperature}
          onChange={(e) => onTemperatureChange(Number(e.target.value))}
          className="w-full accent-primary"
        />
      </div>

      <div className="flex items-start gap-2">
        <input
          id="chat-websearch"
          aria-label="Web search"
          type="checkbox"
          checked={webSearch}
          onChange={(e) => onWebSearchChange(e.target.checked)}
          className="mt-0.5 size-4 accent-primary"
        />
        <label htmlFor="chat-websearch" className="text-xs text-foreground">
          <span className="font-medium">Web search</span>
          <span className="block text-muted-foreground">
            Ground replies with live web results (provider-native).
          </span>
        </label>
      </div>
    </div>
  );
}
