"use client";

/**
 * components/chat/InspectorPanel.tsx — the chat-playground right rail.
 *
 * A Console-grade tabbed control surface: Parameters (default) · Tools · Code.
 * This SHELL task owns the region anatomy; the sibling tasks wire the new
 * capability:
 *   - Parameters: hosts the relocated ModelControls (System prompt / Temperature /
 *     Web search) PLUS scaffolded sampling slots (top_p · max_tokens · penalties ·
 *     seed · stop · response_format) that `chat-parameters-panel` makes live.
 *   - Tools: scaffold for `chat-tools-functions` (define JSON-schema tools).
 *   - Code: scaffold for the request/response code view.
 *
 * The tablist is the shadcn Tabs primitive (role=tablist/tab/tabpanel); Parameters
 * is mounted by default so the relocated controls are reachable without a click.
 */

import { Code2, SlidersHorizontal, Wrench } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Empty } from "@/components/ui/states";
import { ModelControls, type ModelControlsProps } from "@/components/chat/ModelControls";
import { ParamNumber, ParamSegmented, ParamSlider, ParamTags } from "@/components/chat/ParameterField";
import { isSupported, providerLabel, type CapKey } from "@/lib/chat/param-capabilities";
import type { ResponseFormat } from "@/lib/hooks/use-chat-stream";

/** The live sampling state lifted to ChatWorkspace (in-memory, persists across turns + tab switches). */
export interface SamplingState {
  topP?: number;
  maxTokens?: number;
  frequencyPenalty?: number;
  presencePenalty?: number;
  seed?: number;
  stop: string[];
  responseFormat: ResponseFormat;
}

export const EMPTY_SAMPLING: SamplingState = { stop: [], responseFormat: "text" };

export type InspectorPanelProps = ModelControlsProps & {
  /** The selected model id — drives provider-aware capability gating of the controls. */
  model: string;
  sampling: SamplingState;
  onSampling: (patch: Partial<SamplingState>) => void;
};

export function InspectorPanel({ model, sampling, onSampling, ...props }: InspectorPanelProps) {
  // A control the provider can't honor is disabled + annotated, never silently sent.
  const gate = (key: CapKey) =>
    isSupported(model, key) ? {} : { disabled: true, note: `Ignored by ${providerLabel(model)}` };

  return (
    <aside
      className="hidden h-full min-h-0 w-80 flex-shrink-0 flex-col border-l border-border bg-background xl:flex"
      aria-label="Inspector"
    >
      <Tabs defaultValue="parameters" className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList className="m-2 grid grid-cols-3">
          <TabsTrigger value="parameters">
            <SlidersHorizontal className="size-3.5" aria-hidden="true" />
            Parameters
          </TabsTrigger>
          <TabsTrigger value="tools">
            <Wrench className="size-3.5" aria-hidden="true" />
            Tools
          </TabsTrigger>
          <TabsTrigger value="code">
            <Code2 className="size-3.5" aria-hidden="true" />
            Code
          </TabsTrigger>
        </TabsList>

        <TabsContent
          value="parameters"
          className="min-h-0 flex-1 overflow-y-auto px-4 pb-4"
        >
          <ModelControls {...props} />

          {/* Live sampling controls (chat-parameters-panel). Each value is sent on the next
              run only when set + valid + honored by the model's provider; unset/default ⇒
              omitted (byte-identical off path). Provider-unsupported controls are gated. */}
          <div className="mt-6 flex flex-col gap-4 border-t border-border pt-4">
            <span className="text-xs font-medium text-foreground">Sampling</span>
            <ParamSlider
              label="Top P"
              value={sampling.topP}
              defaultPos={1}
              min={0}
              max={1}
              step={0.05}
              onValue={(n) => onSampling({ topP: n })}
            />
            <ParamNumber
              label="Max tokens"
              value={sampling.maxTokens}
              min={1}
              placeholder="auto"
              onValue={(n) => onSampling({ maxTokens: n })}
            />
            <ParamSlider
              label="Frequency penalty"
              value={sampling.frequencyPenalty}
              defaultPos={0}
              min={-2}
              max={2}
              step={0.1}
              onValue={(n) => onSampling({ frequencyPenalty: n })}
              {...gate("frequencyPenalty")}
            />
            <ParamSlider
              label="Presence penalty"
              value={sampling.presencePenalty}
              defaultPos={0}
              min={-2}
              max={2}
              step={0.1}
              onValue={(n) => onSampling({ presencePenalty: n })}
              {...gate("presencePenalty")}
            />
            <ParamNumber
              label="Seed"
              value={sampling.seed}
              placeholder="—"
              onValue={(n) => onSampling({ seed: n })}
              {...gate("seed")}
            />
            <ParamTags
              label="Stop sequences"
              values={sampling.stop}
              onValues={(v) => onSampling({ stop: v })}
            />
            <ParamSegmented<ResponseFormat>
              label="Response format"
              value={sampling.responseFormat}
              options={[
                { value: "text", label: "Text" },
                { value: "json_object", label: "JSON" },
              ]}
              onValue={(v) => onSampling({ responseFormat: v })}
              {...gate("responseFormat")}
            />
          </div>
        </TabsContent>

        <TabsContent value="tools" className="min-h-0 flex-1 overflow-y-auto p-4">
          <Empty
            title="No tools defined"
            description="Define JSON-schema tools to let the model call functions. Coming in this workspace."
          />
        </TabsContent>

        <TabsContent value="code" className="min-h-0 flex-1 overflow-y-auto p-4">
          <Empty
            title="Request preview"
            description="The API request and response for the current run will appear here."
          />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
