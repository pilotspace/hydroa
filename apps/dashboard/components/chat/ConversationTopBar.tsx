"use client";

/**
 * components/chat/ConversationTopBar.tsx — the conversation column header.
 *
 * The compact top bar of the playground's center pane: the conversation title
 * (the page's single h1, with a scaffold rename affordance), the model selector
 * slot, scaffold Fork / Export / View-code actions (wired by chat-conversation-mgmt),
 * and the running-cost chip (CostReadout). The model picker + cost readout are
 * passed in as slots so ChatWorkspace keeps their preserved seams (testids/slots)
 * intact through the reshape.
 */

import type { ReactNode } from "react";
import { Download, GitFork, Code2, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface ConversationTopBarProps {
  title: string;
  modelPicker: ReactNode;
  costReadout: ReactNode;
}

export function ConversationTopBar({ title, modelPicker, costReadout }: ConversationTopBarProps) {
  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-background px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-1.5">
        <h1 className="truncate text-sm font-semibold text-foreground">{title}</h1>
        {/* Scaffold: rename ships in chat-conversation-mgmt. */}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled
          aria-label="Rename conversation"
          className="size-7 text-muted-foreground"
        >
          <Pencil className="size-3.5" aria-hidden="true" />
        </Button>
      </div>

      <div className="ml-auto flex flex-wrap items-center gap-2">
        {/* SLOT: chat-model-controls (picker) */}
        <span data-slot="model-picker">{modelPicker}</span>

        {/* Scaffold actions — wired by chat-conversation-mgmt / code view. */}
        <div className="flex items-center gap-0.5">
          <Button type="button" variant="ghost" size="sm" disabled aria-label="Fork conversation" className="text-muted-foreground">
            <GitFork className="size-3.5" aria-hidden="true" />
            <span className="sr-only sm:not-sr-only">Fork</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" disabled aria-label="Export conversation" className="text-muted-foreground">
            <Download className="size-3.5" aria-hidden="true" />
            <span className="sr-only sm:not-sr-only">Export</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" disabled aria-label="View code" className="text-muted-foreground">
            <Code2 className="size-3.5" aria-hidden="true" />
            <span className="sr-only sm:not-sr-only">Code</span>
          </Button>
        </div>

        {/* chat-cost-readout — running session token total (slot session-cost). */}
        {costReadout}
      </div>
    </header>
  );
}
