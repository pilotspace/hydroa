/**
 * tests/art12-bundle-assembly.test.ts — pure unit suite for lib/art12-bundle.ts
 * (compliance-report-center TASK.md §3 CONTRACT — FROZEN @ v1, M2/M11/M14/R7).
 *
 * No React/DOM dependency — a hand-built mock `fetchPage`, no MSW, mirrors
 * tests/format.test.ts's own pure-lib precedent.
 */
import { describe, it, expect, vi } from "vitest";
import {
  assembleArt12Bundle,
  BundleTooLargeError,
  type Art12BundleResponse,
  type CoverModel,
} from "@/lib/art12-bundle";

const SINCE = "2026-06-01T00:00:00.000Z";
const UNTIL = "2026-06-30T00:00:00.000Z";

function makeCover(overrides: Partial<CoverModel> = {}): CoverModel {
  return {
    bundle_id: "bundle-1",
    generated_at: "2026-07-01T00:00:00Z",
    tenant_id: "tenant-1",
    tenant_name: "Acme",
    period: { since: SINCE, until: UNTIL },
    residency_pin: "eu",
    zdr_state: { enabled: false, enabled_at: null },
    retention_window_days: 90,
    guardrail_configs_snapshot: {},
    default_tier: "standard",
    format_version: "1",
    ...overrides,
  };
}

function emptySection<T>(overrides: Partial<{ items: T[]; has_more: boolean; note: string | null }> = {}) {
  return {
    items: (overrides.items ?? []) as T[],
    next_cursor: null,
    has_more: overrides.has_more ?? false,
    note: overrides.note ?? null,
  };
}

function makePage(overrides: Partial<Art12BundleResponse> = {}): Art12BundleResponse {
  return {
    cover: makeCover(),
    sections: {
      audit_events: emptySection(),
      request_log_metadata: emptySection(),
      usage_lineage: emptySection(),
    },
    bundle_token: null,
    ...overrides,
  };
}

describe("assembleArt12Bundle", () => {
  it("test_assemble_bundle_pure_function_multipage: walks every page, accumulates items, echoes since/until", async () => {
    const page1 = makePage({
      sections: {
        audit_events: emptySection({
          items: [{ id: "a1" }] as unknown as never[],
          has_more: true,
        }),
        request_log_metadata: emptySection(),
        usage_lineage: emptySection(),
      },
      bundle_token: "tok-1",
    });
    const page2 = makePage({
      sections: {
        audit_events: emptySection({ items: [{ id: "a2" }] as unknown as never[], has_more: false }),
        request_log_metadata: emptySection(),
        usage_lineage: emptySection(),
      },
      bundle_token: null,
    });

    const fetchPage = vi.fn(async (since: string, until: string, bundleToken?: string) => {
      expect(since).toBe(SINCE);
      expect(until).toBe(UNTIL);
      return bundleToken === undefined ? page1 : page2;
    });

    const result = await assembleArt12Bundle(fetchPage, SINCE, UNTIL);

    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(fetchPage).toHaveBeenNthCalledWith(1, SINCE, UNTIL, undefined);
    expect(fetchPage).toHaveBeenNthCalledWith(2, SINCE, UNTIL, "tok-1");
    expect(result.sections.audit_events.items).toEqual([{ id: "a1" }, { id: "a2" }]);
    expect(result.cover).toEqual(page1.cover);
  });

  it("carries the note from whichever page sets it", async () => {
    const page1 = makePage({
      sections: {
        audit_events: emptySection({ has_more: true }),
        request_log_metadata: emptySection({ note: "ZDR is enabled" }),
        usage_lineage: emptySection(),
      },
      bundle_token: "tok-1",
    });
    const page2 = makePage({ bundle_token: null });
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2);

    const result = await assembleArt12Bundle(fetchPage, SINCE, UNTIL);
    expect(result.sections.request_log_metadata.note).toBe("ZDR is enabled");
  });

  it("test_page_ceiling_aborts_locally: throws BundleTooLargeError at the ceiling, never makes the over-limit call", async () => {
    let calls = 0;
    const fetchPage = vi.fn(async () => {
      calls += 1;
      return makePage({
        sections: {
          audit_events: emptySection({ has_more: true }),
          request_log_metadata: emptySection(),
          usage_lineage: emptySection(),
        },
        bundle_token: `tok-${calls}`,
      });
    });

    await expect(
      assembleArt12Bundle(fetchPage, SINCE, UNTIL, { maxPages: 5 }),
    ).rejects.toBeInstanceOf(BundleTooLargeError);
    expect(calls).toBe(5); // never a 6th call
  });

  it("propagates a fetchPage rejection unchanged (no retry, no partial result)", async () => {
    const boom = new Error("401 unauthorized");
    const fetchPage = vi.fn().mockRejectedValueOnce(boom);

    await expect(assembleArt12Bundle(fetchPage, SINCE, UNTIL)).rejects.toBe(boom);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });

  it("a single-page walk (has_more=false immediately) resolves after exactly one call", async () => {
    const fetchPage = vi.fn().mockResolvedValueOnce(makePage());
    const result = await assembleArt12Bundle(fetchPage, SINCE, UNTIL);
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(result.sections.audit_events.items).toEqual([]);
  });
});
