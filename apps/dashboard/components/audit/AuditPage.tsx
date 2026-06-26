"use client";

/**
 * AuditPage — the dashboard Audit Log surface (admin-only nav). Reads the tenant-scoped
 * audit event history from GET /admin/audit (via the BFF catch-all) and renders it in
 * a table.
 *
 * AUDIT_READ is enforced by the gateway: owner/admin/operator pass; billing_admin/viewer/
 * member get 403. This page is a read-only viewer — the four states (loading / error /
 * empty / success) are rendered identically to every other dashboard surface.
 *
 * Auth guard: proxy.ts handles cookie-presence server-side. The admin-only nav link
 * and the gateway's AUDIT_READ enforcement keep restricted roles out.
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { AuditTable, AuditData } from "./AuditTable";

export function AuditPage() {
  const auditQuery = useQuery<AuditData>({
    queryKey: ["admin-audit"],
    queryFn: () => bffGet<AuditData>("/admin/audit"),
  });

  return (
    <section
      aria-labelledby="audit-heading"
      className="flex flex-col gap-6"
    >
      <h1
        id="audit-heading"
        className="text-2xl font-semibold tracking-tight text-foreground"
      >
        Audit Log
      </h1>

      {auditQuery.isLoading ? (
        <Loading label="Loading audit log…" />
      ) : auditQuery.isError ? (
        <ErrorState
          title={
            auditQuery.error instanceof Error
              ? auditQuery.error.message
              : "Failed to load audit log"
          }
        />
      ) : (
        <AuditTable data={auditQuery.data} />
      )}
    </section>
  );
}
