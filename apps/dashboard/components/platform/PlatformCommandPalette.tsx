"use client";

/**
 * PlatformCommandPalette — command-palette TASK.md (M1-M16): a superadmin-only,
 * keyboard-triggered (Cmd+K / Ctrl+K) global overlay, reachable from any
 * dashboard route, that searches tenants by name (reusing the existing GET
 * /admin/platform/tenants endpoint — platform_tenants_router.py, FROZEN @ v1,
 * zero backend change) and navigates directly to a selected tenant's detail
 * page. Navigate-only: no command execution beyond search + select + navigate
 * + close (R1).
 *
 * Zero props, fully self-contained (§3 CONTRACT) — this component performs NO
 * internal role re-check and makes NO ["current-user"] fetch of its own. It is
 * only ever mounted by DashboardShell when useCurrentUser().data?.role ===
 * "superadmin" EXACTLY (M1/R2); its own mere presence in the tree is the
 * frontend gate (mirrors app-shell.tsx's own showPlatformNav fail-CLOSED
 * pattern). The backend's existing require_superadmin (403 ERR_AUTH_FORBIDDEN
 * on GET /admin/platform/tenants) and authorize_tenant_scope (destination
 * fetch) gates remain the authoritative, independent, UNMODIFIED
 * defense-in-depth layer underneath this UI-layer gate (R3) — neither is
 * touched by this task.
 *
 * Query pattern mirrors PlatformTenantDirectory.tsx's own debounce/query shape
 * (300ms setTimeout debounce, retry:false) EXACTLY, but with a DELIBERATELY
 * DISTINCT queryKey (["platform-tenant-search", debouncedQuery] vs that
 * component's own ["platform-tenants", debouncedQuery, offset]) and a smaller
 * PALETTE_RESULT_LIMIT=8 (vs that component's PAGE_LIMIT=20) — sharing the
 * identical key under a different limit would silently clobber whichever
 * fetch ran last (TASK.md §0 Issues/Risks #4, R4), so the two never share a
 * cache entry, by design, even though they hit the same backend endpoint.
 *
 * Listbox mirrors MemoryLibraryPane.tsx's own real-ARIA-listbox shape
 * (role="listbox" + role="option" + aria-selected, clamped ArrowUp/ArrowDown)
 * — the one existing precedent in this codebase — with ONE deliberate,
 * already-frozen departure: highlightedIndex initializes to (and re-anchors
 * at) 0 whenever a new non-empty result set loads, so Enter navigates to the
 * top match with no prior ArrowDown required (§3 CONTRACT). The Arrow/Enter
 * keydown handler is bound on the DialogContent ("the palette root", per the
 * contract's own explicit "listbox / palette root" wording) rather than only
 * the <ul> — so it still fires while focus remains in the search Input (the
 * expected command-palette UX: type, then Enter immediately, never requiring
 * a Tab away from the input first).
 *
 * Built on the existing Dialog/DialogContent (Radix, components/ui/dialog.tsx)
 * — the SAME primitive AppShell itself already uses for its own mobile nav
 * sheet — inheriting focus-trap, Escape-to-close, backdrop-click-to-close, and
 * focus-return NATIVELY (M5), zero new focus-management code. open/
 * onOpenChange are CONTROLLED by this component's own `open` state (not
 * DialogTrigger's internal uncontrolled state) because the global keyboard
 * shortcut must ALSO drive it, not only a click on the trigger.
 */

import * as React from "react";
import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { cn } from "@/lib/cn";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Loading, ErrorState, Empty } from "@/components/ui/states";
import type { PlatformTenantSummary } from "./PlatformTenantDirectory";

const SEARCH_DEBOUNCE_MS = 300;
/** Deliberately independent of PlatformTenantDirectory's own PAGE_LIMIT=20 —
 * see the file header + TASK.md §0 Issues/Risks #4. */
const PALETTE_RESULT_LIMIT = 8;

/** Local envelope type only — PlatformTenantDirectory.tsx stays byte-identical
 * (TASK.md §0 Issues/Risks #6): only its already-exported PlatformTenantSummary
 * is imported below; this small wrapper is not re-declared there. */
interface TenantSearchResponse {
  tenants: PlatformTenantSummary[];
  total: number;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function PlatformCommandPalette() {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [debouncedQuery, setDebouncedQuery] = React.useState("");
  const [highlightedIndex, setHighlightedIndex] = React.useState(0);
  const searchInputRef = React.useRef<HTMLInputElement>(null);

  // Debounced search -> queryKey, mirrors PlatformTenantDirectory's own timer shape (M7).
  React.useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  const trimmedQuery = debouncedQuery.trim();

  const { data, isLoading, isError, error } = useQuery<TenantSearchResponse>({
    queryKey: ["platform-tenant-search", debouncedQuery],
    queryFn: () =>
      bffGet<TenantSearchResponse>(
        `/admin/platform/tenants?q=${encodeURIComponent(debouncedQuery)}&limit=${PALETTE_RESULT_LIMIT}&offset=0`,
      ),
    enabled: trimmedQuery.length > 0,
    retry: false,
  });

  const tenants = data?.tenants ?? [];
  const total = data?.total ?? 0;

  // UNLIKE MemoryLibraryPane's own -1-until-first-ArrowDown precedent: the
  // first result is auto-highlighted the instant a new non-empty result set
  // loads (§3 CONTRACT, §0 Issues/Risks #3 — a deliberate, already-frozen
  // departure flagged at freeze). Uses the "adjust state during render"
  // pattern (React docs: "Storing information from previous renders") rather
  // than a setState-in-effect — mirrors PlatformTenantDirectory.tsx's own
  // prevDebouncedQuery/setOffset(0) idiom and PlatformTenantDetail.tsx's own
  // prevSearchParams idiom, both already established in this codebase.
  const [prevData, setPrevData] = React.useState(data);
  if (data !== prevData) {
    setPrevData(data);
    if ((data?.tenants?.length ?? 0) > 0) {
      setHighlightedIndex(0);
    }
  }

  function resetPaletteState() {
    setQuery("");
    setDebouncedQuery("");
    setHighlightedIndex(0);
  }

  // M11: closing by ANY means (Escape, backdrop click, selecting a result, or
  // the shortcut again) clears the query so the next open starts fresh.
  function closePalette() {
    resetPaletteState();
    setOpen(false);
  }

  function handleOpenChange(next: boolean) {
    if (next) {
      setOpen(true);
    } else {
      // Radix calls this for Escape / backdrop click / the close (X) button —
      // all non-selecting close paths; never a navigation.
      closePalette();
    }
  }

  function selectTenant(tenant: PlatformTenantSummary) {
    router.push(`/app/platform/tenants/${tenant.id}`);
    closePalette();
  }

  // M3: global Cmd+K / Ctrl+K, attached only while this component is mounted
  // (i.e. only for an already-confirmed superadmin session, M1/R2). Uses the
  // functional setState form so the effect needs no external dependency and
  // registers its document listener exactly once for the component's lifetime.
  React.useEffect(() => {
    function handleGlobalKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((wasOpen) => {
          if (wasOpen) {
            setQuery("");
            setDebouncedQuery("");
            setHighlightedIndex(0);
            return false;
          }
          return true;
        });
      }
    }
    document.addEventListener("keydown", handleGlobalKeyDown);
    return () => document.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  // M9/M10: Arrow keys move (clamped) the highlight; Enter selects the
  // highlighted tenant. Bound on DialogContent (the palette root) so it fires
  // even while focus remains in the search Input — see file header.
  function handlePaletteKeyDown(e: React.KeyboardEvent) {
    if (tenants.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightedIndex((i) => Math.min(i + 1, tenants.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectTenant(tenants[highlightedIndex]);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {/* M4: an always-visible (all breakpoints), independently-clickable
          fallback trigger — the touch/no-keyboard reachability path. Its
          exact placement/styling is cosmetic only (TASK.md §1 Assumptions,
          flagged at freeze), not load-bearing. */}
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label="Search tenants (Command K)"
          className="fixed bottom-4 right-4 z-40 inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground shadow-lg transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Search className="size-4" aria-hidden="true" />
          <span>Search tenants</span>
          <kbd className="rounded border border-border bg-muted px-1 text-xs text-muted-foreground">
            Cmd K
          </kbd>
        </button>
      </DialogTrigger>

      <DialogContent
        onKeyDown={handlePaletteKeyDown}
        onOpenAutoFocus={(e) => {
          // M6: the search Input — not Radix's own default first-focusable
          // element — receives focus automatically on open.
          e.preventDefault();
          searchInputRef.current?.focus();
        }}
        className="top-[10%] max-w-xl translate-y-0 gap-3"
      >
        <DialogTitle className="sr-only">Search tenants</DialogTitle>
        <DialogDescription className="sr-only">
          Search tenants by name and navigate to their detail page.
        </DialogDescription>

        <Input
          ref={searchInputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search tenants…"
          aria-label="Search tenants by name"
        />

        {trimmedQuery === "" && (
          <p className="text-sm text-muted-foreground">Type to search tenants by name</p>
        )}

        {trimmedQuery !== "" && isLoading && (
          <Loading label="Searching tenants" className="animate-pulse" />
        )}

        {trimmedQuery !== "" && isError && !isLoading && (
          <ErrorState title={getErrorTitle(error)} />
        )}

        {trimmedQuery !== "" && !isLoading && !isError && tenants.length === 0 && (
          <Empty title="No tenants found" description="No tenants matched your search." />
        )}

        {trimmedQuery !== "" && !isLoading && !isError && tenants.length > 0 && (
          <>
            <ul role="listbox" aria-label="Tenant search results" className="flex flex-col gap-0.5">
              {tenants.map((tenant, index) => (
                <li
                  key={tenant.id}
                  role="option"
                  aria-selected={index === highlightedIndex}
                  onClick={() => selectTenant(tenant)}
                  className={cn(
                    "cursor-pointer rounded-md px-3 py-2 text-sm text-foreground",
                    index === highlightedIndex ? "bg-muted" : "hover:bg-accent",
                  )}
                >
                  {tenant.name}
                </li>
              ))}
            </ul>
            {/* M15/R6: an informational, non-interactive hint — never pagination. */}
            {total > tenants.length && (
              <p className="text-xs text-muted-foreground">
                Showing {tenants.length} of {total} — refine your search
              </p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
