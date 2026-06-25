"use client";

/**
 * components/chat/ModelControls.tsx — v40 chat-model-controls.
 *
 * Per-request controls for the chat composer: an optional system prompt and a
 * temperature. Collapsed behind a "Model settings" disclosure by default so the
 * composer keeps exactly one textbox until the user opts in (the system field is
 * the second textbox only when expanded). Pure presentation — state is lifted to
 * ChatWorkspace, which threads it into send().
 */

import { useState } from "react";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export interface ModelControlsProps {
  system: string;
  onSystemChange: (s: string) => void;
  temperature: number;
  onTemperatureChange: (t: number) => void;
}

export function ModelControls({
  system,
  onSystemChange,
  temperature,
  onTemperatureChange,
}: ModelControlsProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="w-full">
      <Button
        type="button"
        variant="ghost"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="h-7 px-2 text-xs text-muted-foreground"
      >
        <Settings2 className="size-3.5" aria-hidden="true" />
        Model settings
      </Button>

      {open ? (
        <div className="mt-2 flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="chat-system" className="text-xs font-medium text-foreground">
              System prompt
            </label>
            <Textarea
              id="chat-system"
              aria-label="System prompt"
              rows={2}
              value={system}
              onChange={(e) => onSystemChange(e.target.value)}
              placeholder="Optional — steer the assistant's behavior"
              className="resize-none text-sm"
            />
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="chat-temp" className="text-xs font-medium text-foreground">
              Temperature
            </label>
            <input
              id="chat-temp"
              aria-label="Temperature"
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(e) => onTemperatureChange(Number(e.target.value))}
              className="flex-1 accent-primary"
            />
            <span className="w-8 text-right text-xs tabular-nums text-muted-foreground">
              {temperature.toFixed(1)}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
