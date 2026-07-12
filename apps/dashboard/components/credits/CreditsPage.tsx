"use client";

/**
 * CreditsPage — prepaid balance + top-up history (billing-ui TASK.md §3 CONTRACT M6, R5).
 * A hero StatCard (balance_usd tabular-nums + a grace note when grace_usd > 0) from
 * GET /admin/credits/balance, and a keyset-paginated history table from
 * GET /admin/credits/history. Any authenticated role passes (credits-ledger §3 M10) — no
 * expected-403 branch, unlike Invoices.
 *
 * Zero entries -> ONE unified informative Empty state covering BOTH "genuinely unused" and
 * "credits_gate_enabled=False platform-wide" (R5 — no API signal distinguishes them); the
 * section is NEVER hidden (the nav item and page always render, per M1/M6).
 */

import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, Empty, ErrorState, StatCard } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { formatUsd } from "@/lib/format";
import { CreditsHistoryTable } from "./CreditsHistoryTable";

interface CreditsBalance {
  tenant_id: string;
  balance_usd: string;
  grace_usd: string;
  updated_at: string;
}

interface CreditsHistoryPage {
  entries: Array<{
    id: string;
    entry_type: "topup" | "hold" | "settle" | "release" | "correction";
    amount_usd: string;
    balance_after_usd: string;
    reference_type: string | null;
    reference_id: string | null;
    request_id: string | null;
    created_at: string;
  }>;
  next_cursor: string | null;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load credits";
}

function buildHistoryQuery(cursor: string | undefined): string {
  return cursor ? `/admin/credits/history?cursor=${encodeURIComponent(cursor)}` : "/admin/credits/history";
}

export function CreditsPage() {
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([undefined]);
  const currentCursor = cursorStack[cursorStack.length - 1];

  const balanceQuery = useQuery<CreditsBalance>({
    queryKey: ["admin-credits-balance"],
    queryFn: () => bffGet<CreditsBalance>("/admin/credits/balance"),
    retry: false,
  });

  const historyQuery = useQuery<CreditsHistoryPage>({
    queryKey: ["admin-credits-history", currentCursor],
    queryFn: () => bffGet<CreditsHistoryPage>(buildHistoryQuery(currentCursor)),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const isLoading = balanceQuery.isLoading || (historyQuery.isLoading && !historyQuery.data);
  const isError = balanceQuery.isError || historyQuery.isError;
  const entries = historyQuery.data?.entries ?? [];
  const grace = balanceQuery.data ? Number(balanceQuery.data.grace_usd) : 0;

  return (
    <section aria-labelledby="credits-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Credits"
        titleId="credits-heading"
        description="Prepaid balance and top-up history for this tenant."
      />

      {isLoading ? (
        <Loading label="Loading credits…" />
      ) : isError ? (
        <ErrorState title={getErrorTitle(balanceQuery.error ?? historyQuery.error)} />
      ) : (
        <div className="flex flex-col gap-6">
          <StatCard
            label="Balance"
            value={formatUsd(balanceQuery.data?.balance_usd)}
            valueTestId="credits-balance-value"
            footer={grace > 0 ? `+ ${formatUsd(balanceQuery.data?.grace_usd)} grace` : undefined}
          />

          {entries.length === 0 ? (
            <Empty
              title="No credit activity yet"
              description="Your platform operator manages credit top-ups — contact them to add credits to this account."
            />
          ) : (
            <div className="flex flex-col gap-3">
              <CreditsHistoryTable entries={entries} />
              <div className="flex items-center justify-end gap-3 px-1 text-sm">
                <button
                  type="button"
                  onClick={() => setCursorStack((stack) => (stack.length > 1 ? stack.slice(0, -1) : stack))}
                  disabled={cursorStack.length <= 1}
                  className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const next = historyQuery.data?.next_cursor;
                    if (next) setCursorStack((stack) => [...stack, next]);
                  }}
                  disabled={!historyQuery.data?.next_cursor}
                  className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
