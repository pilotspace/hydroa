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

/** A read-only scaffold row standing in for a not-yet-wired sampling parameter. */
function ScaffoldParam({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="rounded-md border border-border bg-muted/40 px-1.5 py-0.5 tabular-nums text-muted-foreground">
        {value}
      </span>
    </div>
  );
}

export type InspectorPanelProps = ModelControlsProps;

export function InspectorPanel(props: InspectorPanelProps) {
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

          {/* Scaffold: the sampling slots chat-parameters-panel wires live. */}
          <fieldset
            disabled
            className="mt-6 flex flex-col gap-2.5 border-t border-border pt-4"
            aria-label="Advanced sampling (configured in a later step)"
          >
            <legend className="mb-1 text-xs font-medium text-foreground">
              Sampling
              <span className="ml-1 font-normal text-muted-foreground">— more controls coming</span>
            </legend>
            <ScaffoldParam label="Top P" value="1.0" />
            <ScaffoldParam label="Max tokens" value="2048" />
            <ScaffoldParam label="Frequency penalty" value="0.0" />
            <ScaffoldParam label="Presence penalty" value="0.0" />
            <ScaffoldParam label="Seed" value="—" />
            <ScaffoldParam label="Stop sequences" value="none" />
            <ScaffoldParam label="Response format" value="Text" />
          </fieldset>
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
