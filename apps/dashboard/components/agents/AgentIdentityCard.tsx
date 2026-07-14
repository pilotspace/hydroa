"use client";

/**
 * AgentIdentityCard — the signature element (agents-console TASK.md §3 CONTRACT M2,
 * DESIGN.md §4): one directory unit per `agent_principal`, showing principal / resolved
 * owner / budget cap / real current-month spend / last-seen / attached-token count /
 * live-or-killed state. Built from Card/CardHeader/CardTitle/CardContent/CardFooter
 * (unmodified) inside the `grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3`
 * responsive grid the Directory tab lays out (StatCard/PlatformPlanCatalog precedent).
 *
 * Live/Killed state is NEVER color-only (WCAG 1.4.1): each Badge pairs a distinct icon
 * with its text label, mirroring InvoiceStatusSeal's Badge+icon+sr-only idiom. A killed
 * card drops the Kill action ENTIRELY (never a disabled-but-visible button — a killed
 * principal cannot be revived per the frozen contract) and shows a static
 * "Killed «timestamp»" line instead. "Manage tokens" stays available on both states
 * (M5: opened from any card, live or killed).
 *
 * Kill is reused verbatim behind `ConfirmDialog` (the "ZDR typed-confirm idiom" this
 * codebase actually has — a plain-language, irreversibility-naming `description`, no
 * literal type-the-name input) — M4.
 */

import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { Badge, Button, Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui";
import { ConfirmDialog } from "@/components/teams/ConfirmDialog";
import { formatTimestamp, formatUsd } from "@/lib/format";

export interface AgentPrincipal {
  id: string;
  tenant_id: string;
  name: string;
  owner_user_id: string | null;
  monthly_budget_usd: string | null;
  rpm_limit: number | null;
  tpm_limit: number | null;
  created_at: string;
  last_seen_at: string | null;
  killed_at: string | null;
  attached_token_count: number;
  /** agent-identity-governance §3 FROZEN @ v2 (CR-2): a real, never-null 2-dp string —
   *  "0.00" is the counter's own honest floor, never a client-side fabrication. */
  spend_usd_this_month: string;
}

export interface AgentIdentityCardProps {
  agent: AgentPrincipal;
  /** UI-resolved via a usersById join; null -> "No owner set". A resolved id absent
   *  from that join is passed through as the raw owner_user_id (never blank). */
  ownerDisplayName: string | null;
  onKill: (id: string) => Promise<void>;
  onManageTokens: (id: string) => void;
}

function tokenCountLabel(count: number): string {
  return `${count} ${count === 1 ? "token" : "tokens"} attached`;
}

export function AgentIdentityCard({ agent, ownerDisplayName, onKill, onManageTokens }: AgentIdentityCardProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const isLive = agent.killed_at === null;

  return (
    <Card data-testid={`agent-card-${agent.id}`} className="flex flex-col">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <CardTitle>{agent.name}</CardTitle>
        {isLive ? (
          <Badge variant="success" className="gap-1 shrink-0">
            <CheckCircle2 className="size-3" aria-hidden="true" />
            Live
          </Badge>
        ) : (
          <Badge variant="destructive" className="gap-1 shrink-0">
            <XCircle className="size-3" aria-hidden="true" />
            Killed
          </Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-1.5 text-sm">
        <p className="text-foreground">Owner: {ownerDisplayName ?? "No owner set"}</p>
        <p className="text-foreground">
          Cap: {agent.monthly_budget_usd ? `${formatUsd(agent.monthly_budget_usd)}/mo` : "No cap set"}
        </p>
        <p className="text-foreground">Spend: {formatUsd(agent.spend_usd_this_month)}</p>
        <p className="text-muted-foreground">
          Last seen: {agent.last_seen_at ? formatTimestamp(agent.last_seen_at) : "Never authenticated"}
        </p>
        <p className="text-muted-foreground">{tokenCountLabel(agent.attached_token_count)}</p>
        {!isLive ? (
          <p className="text-muted-foreground">Killed {formatTimestamp(agent.killed_at)}</p>
        ) : null}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => onManageTokens(agent.id)}>
          Manage tokens
        </Button>
        {isLive ? (
          <Button type="button" variant="destructive" size="sm" onClick={() => setConfirmOpen(true)}>
            Kill
          </Button>
        ) : null}
      </CardFooter>

      <ConfirmDialog
        open={confirmOpen}
        title={`Kill ${agent.name}?`}
        description="This immediately and permanently revokes every token attached to this agent. A killed agent cannot be revived — this cannot be undone."
        confirmLabel="Kill agent"
        onConfirm={() => onKill(agent.id)}
        onClose={() => setConfirmOpen(false)}
      />
    </Card>
  );
}
