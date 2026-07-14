"use client";

/**
 * AlertDetailDrawer — row drill-down for a single alert event (audit-remediation item 7).
 *
 * Purely presentational: AlertsPage already fetches the full page of alerts up front
 * (GET /admin/alerts), so this needs no query of its own — unlike InvoiceEvidenceDrawer.tsx
 * (which paginates a separate evidence endpoint), it just renders the ALREADY-CLIENT-SIDE
 * AlertRow in full. Reuses the shared Dialog/DrawerContent chrome (focus-trap, Escape-to-close,
 * and focus-return are all Radix defaults inherited from DrawerContent — same structural
 * pattern as InvoiceEvidenceDrawer.tsx).
 *
 * `alert === null` -> closed (mirrors InvoiceEvidenceDrawer's `lineId === null` convention).
 */

import { useEffect, useRef } from "react";
import {
  Badge,
  Dialog,
  DrawerContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import {
  classifyAlertSeverity,
  SEVERITY_LABELS,
  SEVERITY_BADGE_VARIANT,
} from "@/lib/alert-severity";
import type { AlertRow } from "./AlertsTable";

export interface AlertDetailDrawerProps {
  /** null = closed */
  alert: AlertRow | null;
  onClose: () => void;
}

export function AlertDetailDrawer({ alert, onClose }: AlertDetailDrawerProps) {
  // Capture the triggering control at the instant the drawer opens, restore focus to it
  // on close (mirrors InvoiceEvidenceDrawer.tsx / LogDetailDrawer.tsx verbatim).
  const triggerElementRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (alert !== null && typeof document !== "undefined") {
      triggerElementRef.current = document.activeElement as HTMLElement | null;
    }
  }, [alert]);

  const severity = alert ? classifyAlertSeverity(alert.event_type) : "info";
  const hasPayload = !!alert?.payload && Object.keys(alert.payload).length > 0;

  return (
    <Dialog
      open={alert !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          triggerElementRef.current?.focus();
        }}
      >
        <DialogTitle>Alert details</DialogTitle>
        <DialogDescription className="sr-only">
          Full detail for this alert event, including its raw payload.
        </DialogDescription>

        {alert && (
          <div className="mt-4 flex flex-col gap-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground">{alert.event_type}</span>
              <Badge variant={SEVERITY_BADGE_VARIANT[severity]}>{SEVERITY_LABELS[severity]}</Badge>
              {alert.delivered ? (
                <Badge variant="success">Delivered</Badge>
              ) : (
                <Badge variant="secondary">Pending</Badge>
              )}
            </div>

            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">Occurred</dt>
              <dd>{formatTimestamp(alert.created_at)}</dd>
              <dt className="text-muted-foreground">Delivered at</dt>
              <dd>{formatTimestamp(alert.delivered_at)}</dd>
            </dl>

            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Payload
              </span>
              <pre className="max-h-64 overflow-auto rounded-md border border-border bg-muted/30 p-3 font-mono text-xs">
                {hasPayload ? JSON.stringify(alert.payload, null, 2) : "—"}
              </pre>
            </div>
          </div>
        )}
      </DrawerContent>
    </Dialog>
  );
}
