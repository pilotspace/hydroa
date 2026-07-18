"use client";

/**
 * AuthorizationSeal — the ONE marker of a device-authorization's state on /activate
 * (device-activate-page §3 M4/M10). Translated from InvoiceStatusSeal's financial-document
 * idiom: state is conveyed by an ICON + TEXT (never color alone), with sr-only copy naming
 * a terminal state final. WCAG 2.2 AA: every variant uses an AA-safe Badge token
 * (success-text / destructive-text / warning-foreground) and a lucide icon marked
 * aria-hidden so the text carries the meaning for assistive tech.
 */

import { Clock, Lock, ShieldX, TimerOff } from "lucide-react";
import { Badge } from "@/components/ui";

export type AuthorizationSealStatus = "pending" | "granted" | "denied" | "expired";

export interface AuthorizationSealProps {
  status: AuthorizationSealStatus;
}

export function AuthorizationSeal({ status }: AuthorizationSealProps) {
  if (status === "granted") {
    return (
      <Badge variant="success" className="gap-1" data-slot="authorization-seal">
        <Lock className="size-3" aria-hidden="true" />
        Granted
        <span className="sr-only"> — you granted access; this authorization is final</span>
      </Badge>
    );
  }
  if (status === "denied") {
    return (
      <Badge variant="destructive" className="gap-1" data-slot="authorization-seal">
        <ShieldX className="size-3" aria-hidden="true" />
        Denied
        <span className="sr-only"> — you denied access; this authorization is final</span>
      </Badge>
    );
  }
  if (status === "expired") {
    return (
      <Badge variant="secondary" className="gap-1" data-slot="authorization-seal">
        <TimerOff className="size-3" aria-hidden="true" />
        Expired
      </Badge>
    );
  }
  // pending — awaiting the human's decision
  return (
    <Badge variant="warning" className="gap-1" data-slot="authorization-seal">
      <Clock className="size-3" aria-hidden="true" />
      Pending
      <span className="sr-only"> — awaiting your approval</span>
    </Badge>
  );
}
