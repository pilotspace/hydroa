/**
 * lib/art12-bundle.ts — pure, no React/DOM dependency (compliance-report-center
 * TASK.md §3 CONTRACT — FROZEN @ v1; on-demand Art. 12 bundle assembly, UNCHANGED
 * by this revision from the original art12-record-keeping-preset design).
 *
 * Walks the FROZEN `GET /admin/compliance/art12-bundle` route's `bundle_token`
 * continuation until every section reports `has_more=false` (M2/M14). The caller
 * supplies `fetchPage` (a thin wrapper over `bffGet`), so this module never touches
 * `fetch`/`bffGet` itself and stays unit-testable with a hand-built mock.
 *
 * Safety rule (frozen, §5 BUILD): this is a retry-free SINGLE pass. It never retries
 * a failed page and never re-derives `since`/`until` from a live source mid-walk —
 * the exact `since`/`until` strings captured at call time are echoed on every
 * continuation call. Any non-2xx `fetchPage` rejection propagates UNCHANGED (no
 * try/catch here) so the caller can map it to a distinct ErrorState; no partial
 * bundle is ever returned on failure — the function either resolves with a FULLY
 * walked bundle or rejects, never something in between.
 */

export interface PeriodModel {
  since: string;
  until: string;
}

export interface ZdrStateModel {
  enabled: boolean;
  enabled_at: string | null;
}

export interface CoverModel {
  bundle_id: string;
  generated_at: string;
  tenant_id: string;
  tenant_name: string;
  period: PeriodModel;
  residency_pin: string | null;
  zdr_state: ZdrStateModel;
  retention_window_days: number | null;
  guardrail_configs_snapshot: Record<string, unknown>;
  default_tier: string;
  format_version: string;
}

export interface AuditEventItem {
  id: string;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  result: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface LogListItem {
  id: string;
  key_id: string;
  team_id: string | null;
  model_id: string;
  status_code: number;
  stream: boolean;
  cached: boolean;
  scrub_status: string;
  truncated: boolean;
  cost_usd: string | null;
  created_at: string;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
}

export interface UsageLineageItem {
  id: string;
  key_id: string;
  model_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: string;
  cost_basis: string;
  usage_source: string;
  tier_served: string;
  status: number;
  created_at: string;
}

interface SectionPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  note: string | null;
}

export interface Art12BundleResponse {
  cover: CoverModel;
  sections: {
    audit_events: SectionPage<AuditEventItem>;
    request_log_metadata: SectionPage<LogListItem>;
    usage_lineage: SectionPage<UsageLineageItem>;
  };
  bundle_token: string | null;
}

export interface Art12AssembledBundle {
  cover: CoverModel;
  sections: {
    audit_events: { items: AuditEventItem[]; note: string | null };
    request_log_metadata: { items: LogListItem[]; note: string | null };
    usage_lineage: { items: UsageLineageItem[]; note: string | null };
  };
}

/** Thrown when the local page-count ceiling (default 500, M11/R7) is reached BEFORE
 * the would-be-next page is ever fetched — the caller never sees a partial preview. */
export class BundleTooLargeError extends Error {
  constructor(maxPages: number) {
    super(`Art. 12 bundle exceeds the ${maxPages}-page local assembly ceiling`);
    this.name = "BundleTooLargeError";
  }
}

const DEFAULT_MAX_PAGES = 500;

/**
 * Walk every page of the frozen art12-bundle route until all three sections report
 * `has_more=false`, accumulating each section's items in page order. The pinned
 * `cover` is taken from the FIRST page only (every later page echoes the same
 * values verbatim per the frozen contract, so re-reading it would be redundant,
 * not incorrect — but taking page 1's is the simplest, cheapest choice).
 */
export async function assembleArt12Bundle(
  fetchPage: (since: string, until: string, bundleToken?: string) => Promise<Art12BundleResponse>,
  since: string,
  until: string,
  opts?: { maxPages?: number },
): Promise<Art12AssembledBundle> {
  const maxPages = opts?.maxPages ?? DEFAULT_MAX_PAGES;

  let cover: CoverModel | null = null;
  const auditItems: AuditEventItem[] = [];
  const logItems: LogListItem[] = [];
  const usageItems: UsageLineageItem[] = [];
  let auditNote: string | null = null;
  let logNote: string | null = null;
  let usageNote: string | null = null;

  let bundleToken: string | undefined;
  let page = 0;
  let hasMore = true;

  while (hasMore) {
    page += 1;
    if (page > maxPages) {
      throw new BundleTooLargeError(maxPages);
    }

    // The SAME since/until captured at call time — never re-derived mid-walk.
    const resp = await fetchPage(since, until, bundleToken);

    if (cover === null) {
      cover = resp.cover;
    }

    auditItems.push(...resp.sections.audit_events.items);
    logItems.push(...resp.sections.request_log_metadata.items);
    usageItems.push(...resp.sections.usage_lineage.items);

    if (resp.sections.audit_events.note !== null) auditNote = resp.sections.audit_events.note;
    if (resp.sections.request_log_metadata.note !== null) {
      logNote = resp.sections.request_log_metadata.note;
    }
    if (resp.sections.usage_lineage.note !== null) usageNote = resp.sections.usage_lineage.note;

    hasMore =
      resp.sections.audit_events.has_more ||
      resp.sections.request_log_metadata.has_more ||
      resp.sections.usage_lineage.has_more;
    bundleToken = resp.bundle_token ?? undefined;
    if (hasMore && bundleToken === undefined) {
      // Defensive: has_more=true with no continuation token would loop forever —
      // stop rather than hang (never observed from the real backend, which always
      // pairs has_more=true with a bundle_token).
      break;
    }
  }

  if (cover === null) {
    // Unreachable in practice (the loop always runs at least once), but keeps the
    // return type honest without a non-null assertion.
    throw new Error("assembleArt12Bundle: no page was ever fetched");
  }

  return {
    cover,
    sections: {
      audit_events: { items: auditItems, note: auditNote },
      request_log_metadata: { items: logItems, note: logNote },
      usage_lineage: { items: usageItems, note: usageNote },
    },
  };
}
