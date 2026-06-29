"use client";

/**
 * components/chat/ToolsEditor.tsx — the inspector Tools tab.
 *
 * chat-tools-functions. An Auto/Required/None tool_choice selector + a list of raw-JSON
 * tool editors (one OpenAI function schema each). Each draft is shape-validated live
 * (parseToolDef): a valid draft shows a Valid badge and rides the next run; an invalid
 * one is flagged and excluded from the wire. State lives in ChatWorkspace so it survives
 * tab switches; this component is presentation + edit callbacks only.
 */

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ParamSegmented } from "@/components/chat/ParameterField";
import { parseToolDef, type ToolChoice } from "@/lib/chat/tool-defs";

const PLACEHOLDER = `{
  "name": "get_weather",
  "description": "Get the weather for a city",
  "parameters": {
    "type": "object",
    "properties": { "city": { "type": "string" } },
    "required": ["city"]
  }
}`;

export interface ToolsEditorProps {
  drafts: string[];
  onDrafts: (next: string[]) => void;
  toolChoice: ToolChoice;
  onToolChoice: (next: ToolChoice) => void;
}

export function ToolsEditor({ drafts, onDrafts, toolChoice, onToolChoice }: ToolsEditorProps) {
  const validCount = drafts.filter((d) => parseToolDef(d) !== null).length;

  return (
    <div className="flex flex-col gap-4">
      <ParamSegmented<ToolChoice>
        label="Tool choice"
        value={toolChoice}
        options={[
          { value: "auto", label: "Auto" },
          { value: "required", label: "Required" },
          { value: "none", label: "None" },
        ]}
        onValue={onToolChoice}
      />

      <div className="flex flex-col gap-3 border-t border-border pt-4">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-foreground">Tools</span>
          <span className="text-xs text-muted-foreground">{validCount} valid · sent next run</span>
        </div>

        {drafts.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No tools yet. Add a JSON function definition to let the model call it.
          </p>
        ) : null}

        {drafts.map((draft, i) => {
          const state =
            draft.trim() === "" ? "empty" : parseToolDef(draft) !== null ? "valid" : "invalid";
          return (
            <div key={i} className="flex flex-col gap-1 rounded-lg border border-border p-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-foreground">Tool {i + 1}</span>
                <div className="flex items-center gap-2">
                  {state === "valid" ? (
                    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                      Valid
                    </span>
                  ) : state === "invalid" ? (
                    <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-[10px] font-medium text-destructive">
                      Invalid JSON
                    </span>
                  ) : null}
                  <button
                    type="button"
                    aria-label={`Remove tool ${i + 1}`}
                    onClick={() => onDrafts(drafts.filter((_, j) => j !== i))}
                    className="text-muted-foreground transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Trash2 className="size-3.5" aria-hidden="true" />
                  </button>
                </div>
              </div>
              <Textarea
                aria-label="Tool definition"
                value={draft}
                onChange={(e) => onDrafts(drafts.map((d, j) => (j === i ? e.target.value : d)))}
                placeholder={PLACEHOLDER}
                rows={8}
                className="resize-y font-mono text-xs"
              />
            </div>
          );
        })}

        <Button type="button" variant="outline" size="sm" onClick={() => onDrafts([...drafts, ""])}>
          <Plus className="size-4" aria-hidden="true" />
          Add tool
        </Button>
      </div>
    </div>
  );
}
