"use client";

/**
 * GeneratedReportsList — the in-app inbox of every compliance_report_runs row a
 * tenant's scheduler has produced (compliance-report-center TASK.md §3 CONTRACT —
 * FROZEN @ v1, M18/R11/R13). Keyset-paginated via useInfiniteQuery, generated_at
 * DESC (mirrors the frozen GET /admin/compliance/reports shape).
 *
 * Each row's download is a real anchor pointing at the BFF pass-through path
 * (`/api/gw/admin/compliance/reports/{id}`, right-click / open-in-new-tab still
 * works) whose primary click path is intercepted to fetch first: a 503
 * (ERR_OBJECT_STORE_UNAVAILABLE, R13) or 404 renders an inline ROW-level error
 * without blanking the rest of the list; a 2xx triggers a Blob download using the
 * response's own Content-Disposition filename.
 */

import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import {
  Button,
  Loading,
  ErrorState,
  Empty,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui";
import { BffError } from "@/lib/bff-client";
import { listGeneratedReports, type GeneratedReportSummary } from "@/lib/compliance-reports";
import { formatTimestamp, formatNumber } from "@/lib/format";

const REPORTS_QUERY_KEY = ["compliance-generated-reports"];

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

function filenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const match = /filename="([^"]+)"/.exec(disposition);
  return match ? match[1] : fallback;
}

export function GeneratedReportsList() {
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const { data, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useInfiniteQuery({
      queryKey: REPORTS_QUERY_KEY,
      queryFn: ({ pageParam }) => listGeneratedReports(pageParam),
      initialPageParam: undefined as string | undefined,
      getNextPageParam: (lastPage) => (lastPage.hasMore ? (lastPage.nextCursor ?? undefined) : undefined),
    });

  async function performRowDownload(
    event: React.MouseEvent<HTMLAnchorElement>,
    report: GeneratedReportSummary,
  ) {
    event.preventDefault();
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[report.id];
      return next;
    });
    setDownloadingId(report.id);
    try {
      const res = await fetch(`/api/gw/admin/compliance/reports/${report.id}`, {
        method: "GET",
        credentials: "include",
      });
      if (!res.ok) {
        let title = "Download failed";
        try {
          const body = (await res.json()) as { title?: string };
          title = body.title ?? title;
        } catch {
          // non-JSON error body — keep the generic title
        }
        setRowErrors((prev) => ({ ...prev, [report.id]: title }));
        return;
      }
      const filename = filenameFromDisposition(
        res.headers.get("content-disposition"),
        `art12-bundle-${report.id}.json`,
      );
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch {
      setRowErrors((prev) => ({ ...prev, [report.id]: "Download failed" }));
    } finally {
      setDownloadingId(null);
    }
  }

  if (isLoading) {
    return <Loading label="Loading generated reports" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  const items: GeneratedReportSummary[] = data?.pages.flatMap((p) => p.items) ?? [];

  if (items.length === 0) {
    return (
      <Empty
        title="No reports generated yet"
        description="Enable Scheduled generation above, or check back after the next monthly run."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Period</TableHead>
            <TableHead>Generated</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Download</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((report) => (
            <TableRow key={report.id}>
              <TableCell>
                {formatTimestamp(report.periodStart)} – {formatTimestamp(report.periodEnd)}
              </TableCell>
              <TableCell>{formatTimestamp(report.generatedAt)}</TableCell>
              <TableCell>{formatNumber(report.sizeBytes)} bytes</TableCell>
              <TableCell>
                <a
                  href={`/api/gw/admin/compliance/reports/${report.id}`}
                  onClick={(e) => {
                    void performRowDownload(e, report);
                  }}
                  className="text-sm font-medium text-primary underline underline-offset-2"
                >
                  {downloadingId === report.id ? "Downloading…" : "Download"}
                </a>
                {rowErrors[report.id] && (
                  <p role="alert" aria-live="polite" className="text-xs text-destructive">
                    {rowErrors[report.id]}
                  </p>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {hasNextPage && (
        <div>
          <Button
            type="button"
            variant="secondary"
            disabled={isFetchingNextPage}
            onClick={() => {
              void fetchNextPage();
            }}
          >
            {isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}
    </div>
  );
}
