"use client";

/**
 * AlertsTable — alert/event history rendered via the shared sortable DataTable block.
 * Columns: Severity (client-side classification, audit-remediation item 7), Type
 * (event_type), When (created_at, humanized), Status (Delivered vs Pending, derived from
 * the `delivered` flag), Payload (the event detail JSON, truncated), Details (row
 * drill-down opening AlertDetailDrawer with the FULL untruncated payload). Zero rows
 * render the shared Empty state ("No alerts yet").
 *
 * The drill-down's open/closed row state lives HERE (not lifted to AlertsPage) since it's
 * pure UI state over data the table already has in full — same "self-contained dialog"
 * shape as KeyGovernanceEditor's own rotate-confirm dialog.
 */

import { useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Badge, Button, DataTable } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { classifyAlertSeverity, SEVERITY_LABELS, SEVERITY_BADGE_VARIANT } from "@/lib/alert-severity";
import { AlertDetailDrawer } from "./AlertDetailDrawer";

export interface AlertRow {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
  delivered: boolean;
  delivered_at: string | null;
}

export interface AlertsData {
  items: AlertRow[];
  total: number;
}

/** Column defs take onViewDetails so the Details button can open THIS table's own
 * drill-down state — built inside the component (not module scope) since it now
 * closes over a callback. */
function buildColumns(onViewDetails: (row: AlertRow) => void): ColumnDef<AlertRow>[] {
  return [
    {
      id: "severity",
      header: "Severity",
      // Client-side classification only (lib/alert-severity.ts) — the backend emits
      // no severity field; text is the a11y contract, tone reinforces it.
      cell: ({ row }) => {
        const severity = classifyAlertSeverity(row.original.event_type);
        return (
          <Badge variant={SEVERITY_BADGE_VARIANT[severity]}>{SEVERITY_LABELS[severity]}</Badge>
        );
      },
    },
    { accessorKey: "event_type", header: "Type" },
    {
      accessorKey: "created_at",
      header: "When",
      cell: ({ row }) => formatTimestamp(row.original.created_at),
    },
    {
      id: "status",
      header: "Status",
      // Semantic Badge — the "Delivered"/"Pending" TEXT is the a11y contract; delivered reads
      // as the success tone, pending as a neutral (not-yet-delivered is not an error).
      cell: ({ row }) =>
        row.original.delivered ? (
          <Badge variant="success">Delivered</Badge>
        ) : (
          <Badge variant="secondary">Pending</Badge>
        ),
    },
    {
      id: "payload",
      header: "Payload",
      cell: ({ row }) => {
        const payload = row.original.payload;
        const text =
          payload && Object.keys(payload).length > 0 ? JSON.stringify(payload) : "—";
        return (
          <code
            className="block max-w-xs truncate font-mono text-xs text-muted-foreground"
            title={text}
          >
            {text}
          </code>
        );
      },
    },
    {
      id: "details",
      header: "Details",
      // Row drill-down (audit-remediation item 7) — opens AlertDetailDrawer with the
      // FULL untruncated payload; the Payload column above stays a truncated preview.
      cell: ({ row }) => (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onViewDetails(row.original)}
        >
          Details
        </Button>
      ),
    },
  ];
}

interface AlertsTableProps {
  data: AlertsData | undefined;
}

export function AlertsTable({ data }: AlertsTableProps) {
  const [selectedAlert, setSelectedAlert] = useState<AlertRow | null>(null);

  if (!data) return null;

  return (
    <>
      <DataTable
        columns={buildColumns(setSelectedAlert)}
        data={data.items}
        ariaLabel="Alert events"
        emptyMessage="No alerts yet"
      />
      <AlertDetailDrawer alert={selectedAlert} onClose={() => setSelectedAlert(null)} />
    </>
  );
}
