/**
 * EvalStatusBadge — evals-console TASK.md §3 CONTRACT M4. A single status vocabulary
 * shared by both worlds this console renders: a RUN's own status
 * ("pending"|"running"|"completed"|"failed") and a CASE's DERIVED outcome
 * ("pass"|"fail"|"refused"|"errored"|"pending" — see deriveCaseOutcome in
 * CaseDiffRow.tsx). Every state carries its OWN icon + text label, never color alone
 * (WCAG 1.4.1) — this is the "never a bare colored dot" requirement.
 */

import { AlertTriangle, CheckCircle2, Clock, HelpCircle, Loader2, ShieldOff, XCircle } from "lucide-react";
import { Badge, type BadgeProps } from "@/components/ui";
import { cn } from "@/lib/cn";

interface StatusConfig {
  label: string;
  variant: NonNullable<BadgeProps["variant"]>;
  Icon: typeof CheckCircle2;
  spin?: boolean;
}

const STATUS_CONFIG: Record<string, StatusConfig> = {
  // derived case outcomes
  pass: { label: "Pass", variant: "success", Icon: CheckCircle2 },
  fail: { label: "Fail", variant: "destructive", Icon: XCircle },
  refused: { label: "Refused", variant: "warning", Icon: ShieldOff },
  errored: { label: "Errored", variant: "destructive", Icon: AlertTriangle },
  pending: { label: "Pending", variant: "outline", Icon: Clock },
  // run statuses
  completed: { label: "Completed", variant: "success", Icon: CheckCircle2 },
  running: { label: "Running", variant: "outline", Icon: Loader2, spin: true },
  failed: { label: "Failed", variant: "destructive", Icon: XCircle },
};

export interface EvalStatusBadgeProps {
  status: string;
  className?: string;
}

export function EvalStatusBadge({ status, className }: EvalStatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? { label: status, variant: "outline" as const, Icon: HelpCircle };
  const { label, variant, Icon, spin } = config;
  return (
    <Badge variant={variant} className={cn("gap-1", className)}>
      <Icon className={cn("size-3", spin && "animate-spin")} aria-hidden="true" />
      {label}
    </Badge>
  );
}
