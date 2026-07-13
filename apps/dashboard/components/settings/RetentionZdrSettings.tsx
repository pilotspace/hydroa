"use client";

/**
 * RetentionZdrSettings — Retention & ZDR tab content for the /settings hub (owner-only,
 * requires SECURITY_CONFIG permission for PUT; GET is any authenticated role).
 *
 * GET/PUT /admin/retention-policy. `window_days` is a nullable numeric input ("inherits
 * operator default" when empty); `operator_ceiling_days` is read-only helper text;
 * `effective_window_days` is a read-only 7-row breakdown table (audit_events NEVER
 * appears — a permanent exclusion per the frozen contract).
 *
 * M9: enabling ZDR (false->true) is destructive/irreversible (a real purge, not a
 * soft-delete — the frozen contract's own scenario: "Disabling ZDR does not
 * retroactively restore purged rows") — gated behind ConfirmDialog; the switch flips
 * optimistically while the dialog is open, then RECONCILES from the query cache (the
 * single source of truth) on close — correct whether the user confirms or cancels,
 * with no dependency on React-render-vs-mutation-resolution ordering.
 * Disabling (true->false) is NOT destructive and fires the PUT immediately, no dialog.
 *
 * M10: when zdr_enabled=true, `window_days`/`effective_window_days` are shown
 * DISABLED + muted, never unmounted — the backend still returns both, hiding either
 * would misrepresent state the API is actively reporting.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import {
  Switch,
  Button,
  Input,
  Loading,
  ErrorState,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui";
import { ConfirmDialog } from "@/components/teams/ConfirmDialog";
import { formatTimestamp } from "@/lib/format";

interface EffectiveWindowDays {
  usage_records: number;
  alert_events: number;
  artifacts: number;
  conversations: number;
  memories: number;
  batch_job_items: number;
  video_generation_jobs: number;
}

interface RetentionPolicy {
  window_days: number | null;
  effective_window_days: EffectiveWindowDays;
  zdr_enabled: boolean;
  zdr_enabled_at: string | null;
  operator_ceiling_days: number;
}

interface RetentionPut {
  window_days?: number;
  zdr_enabled?: boolean;
}

const RETENTION_QUERY_KEY = ["admin-retention-policy"];

/** GET/PUT /admin/residency-policy — residency-policy TASK.md §3, FROZEN @ v2 (CR-1
 * added "ap" to the region enum). residency-tiers-ui TASK.md §3 M1-M5. */
type ResidencyRegion = "us" | "eu" | "ap";

interface ResidencyPolicy {
  region: ResidencyRegion | null;
  updated_at: string | null;
}

const RESIDENCY_QUERY_KEY = ["admin-residency-policy"];

const RESIDENCY_REGION_LABELS: Record<ResidencyRegion, string> = { us: "US", eu: "EU", ap: "AP" };

/** Consequence-line copy (VERBATIM per pin target, residency-tiers-ui TASK.md §3) —
 * the persona's signature element, shown inside ConfirmDialog before a tightening
 * write ever fires. AP's copy (CR-1, "DECIDED at freeze review") joins EU/US, naming
 * the Vietnam/SEA routing consequence per the freeze-review note. */
const RESIDENCY_CONSEQUENCE: Record<ResidencyRegion, string> = {
  eu: "Pinning to EU means requests that cannot run in the EU will be refused, not rerouted. This also blocks realtime voice for this tenant — no realtime model is region-tagged yet.",
  us: "Pinning to US means requests that cannot run in the US will be refused, not rerouted. This also blocks realtime voice for this tenant — no realtime model is region-tagged yet.",
  ap: "Pinning to AP means requests that cannot run in Asia-Pacific will be refused, not rerouted; Vietnam is served from Singapore/SEA endpoints. This also blocks realtime voice for this tenant — no realtime model is region-tagged yet.",
};

/** Store display order — exactly the 7 swept classes; audit_events is permanently excluded. */
const STORE_LABELS: { key: keyof EffectiveWindowDays; label: string }[] = [
  { key: "usage_records", label: "Usage records" },
  { key: "alert_events", label: "Alert events" },
  { key: "artifacts", label: "Artifacts" },
  { key: "conversations", label: "Conversations" },
  { key: "memories", label: "Memories" },
  { key: "batch_job_items", label: "Batch job items" },
  { key: "video_generation_jobs", label: "Video generation jobs" },
];

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function RetentionZdrSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<RetentionPolicy>({
    queryKey: RETENTION_QUERY_KEY,
    queryFn: () => bffGet<RetentionPolicy>("/admin/retention-policy"),
    retry: false,
  });

  const [windowDaysInput, setWindowDaysInput] = useState("");
  const [zdrEnabled, setZdrEnabled] = useState(false);
  const [zdrEnabledAt, setZdrEnabledAt] = useState<string | null>(null);
  const [effective, setEffective] = useState<EffectiveWindowDays | null>(null);
  const [operatorCeiling, setOperatorCeiling] = useState<number | null>(null);
  const [windowError, setWindowError] = useState<string | null>(null);
  const [mutError, setMutError] = useState<string | null>(null);
  const [zdrConfirmOpen, setZdrConfirmOpen] = useState(false);

  const [seededData, setSeededData] = useState<RetentionPolicy | undefined>(undefined);
  if (data && data !== seededData) {
    setSeededData(data);
    setWindowDaysInput(data.window_days === null ? "" : String(data.window_days));
    setZdrEnabled(data.zdr_enabled);
    setZdrEnabledAt(data.zdr_enabled_at);
    setEffective(data.effective_window_days);
    setOperatorCeiling(data.operator_ceiling_days);
  }

  // M1: the "Data residency" fieldset's OWN useQuery, independent of the Retention/ZDR
  // read above — never gated by/gating the other two fieldsets (§3 Empty/error matrix).
  const residencyQuery = useQuery<ResidencyPolicy>({
    queryKey: RESIDENCY_QUERY_KEY,
    queryFn: () => bffGet<ResidencyPolicy>("/admin/residency-policy"),
    retry: false,
  });

  const [pendingRegion, setPendingRegion] = useState<ResidencyRegion | null>(null);
  const [residencyMutError, setResidencyMutError] = useState<string | null>(null);
  const [residencyConfirmOpen, setResidencyConfirmOpen] = useState(false);

  const [seededResidency, setSeededResidency] = useState<ResidencyPolicy | undefined>(undefined);
  if (residencyQuery.data && residencyQuery.data !== seededResidency) {
    setSeededResidency(residencyQuery.data);
    setPendingRegion(residencyQuery.data.region);
  }

  const saveRetention = useMutation({
    mutationFn: (body: RetentionPut) => bffPut<RetentionPolicy>("/admin/retention-policy", body),
    onSuccess: (resp) => {
      setMutError(null);
      setWindowError(null);
      queryClient.setQueryData<RetentionPolicy>(RETENTION_QUERY_KEY, resp);
    },
    onError: (err) => {
      if (err instanceof BffError && err.status === 422) {
        setWindowError(getErrorTitle(err));
      } else {
        setMutError(getErrorTitle(err));
      }
    },
  });

  function handleSaveWindow() {
    setWindowError(null);
    setMutError(null);
    const trimmed = windowDaysInput.trim();
    const body: RetentionPut = {};
    if (trimmed !== "") {
      body.window_days = Number(trimmed);
    }
    saveRetention.mutate(body);
  }

  function handleZdrToggle(next: boolean) {
    if (next) {
      // M9: enabling ZDR is destructive/irreversible — flip optimistically (the user's
      // click intent), confirm before any PUT fires.
      setZdrEnabled(true);
      setZdrConfirmOpen(true);
      return;
    }
    // Disabling ZDR is NOT destructive — fires immediately, no confirmation (M9 inverse).
    setZdrEnabled(false);
    setMutError(null);
    saveRetention.mutate({ zdr_enabled: false });
  }

  async function handleConfirmZdrEnable() {
    await saveRetention.mutateAsync({ zdr_enabled: true });
  }

  function handleZdrConfirmClose() {
    setZdrConfirmOpen(false);
    // Reconcile the switch from the query cache (authoritative, mutated synchronously
    // by onSuccess regardless of React re-render timing) — covers BOTH "user cancelled"
    // (cache still holds the pre-toggle false) and "confirm already succeeded" (cache
    // already holds true) without any ordering assumption between them.
    const known = queryClient.getQueryData<RetentionPolicy>(RETENTION_QUERY_KEY);
    setZdrEnabled(known?.zdr_enabled ?? false);
  }

  const saveResidency = useMutation({
    mutationFn: (body: { region: ResidencyRegion | null }) =>
      bffPut<ResidencyPolicy>("/admin/residency-policy", body),
    onSuccess: (resp) => {
      setResidencyMutError(null);
      queryClient.setQueryData<ResidencyPolicy>(RESIDENCY_QUERY_KEY, resp);
    },
    onError: (err) => {
      // R2/R3: never write an unconfirmed pending value — revert the displayed
      // selection to the last-known-good server state, mirrors handleZdrConfirmClose.
      const known = queryClient.getQueryData<ResidencyPolicy>(RESIDENCY_QUERY_KEY);
      setPendingRegion(known?.region ?? null);
      setResidencyMutError(getErrorTitle(err));
    },
  });

  function handleResidencySave() {
    setResidencyMutError(null);
    const current = residencyQuery.data?.region ?? null;
    if (pendingRegion === current) return; // nothing changed
    if (pendingRegion === null) {
      // M4: loosening (clearing a pin) is safe — fires immediately, no confirm.
      saveResidency.mutate({ region: null });
      return;
    }
    // M3: unset->pinned AND pinned A->pinned B are BOTH confirm-gated.
    setResidencyConfirmOpen(true);
  }

  async function handleConfirmResidencyPin() {
    if (pendingRegion === null) return;
    await saveResidency.mutateAsync({ region: pendingRegion });
  }

  function handleResidencyConfirmClose() {
    setResidencyConfirmOpen(false);
    const known = queryClient.getQueryData<ResidencyPolicy>(RESIDENCY_QUERY_KEY);
    setPendingRegion(known?.region ?? null);
  }

  if (isLoading) {
    return <Loading label="Loading retention & ZDR settings" />;
  }

  // R(403) / cross-tenant 404 (ERR_TENANT_NOT_FOUND): ErrorState, no form — never a
  // hint that another tenant's policy exists.
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  return (
    <div className="flex flex-col gap-6">
      <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
        <legend className="px-1 text-sm font-semibold text-foreground">Retention window</legend>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="retention-window-days" className="text-sm font-medium text-foreground">
            Window (days)
          </label>
          <Input
            id="retention-window-days"
            type="number"
            value={windowDaysInput}
            disabled={zdrEnabled}
            onChange={(e) => setWindowDaysInput(e.target.value)}
            placeholder="Inherits operator default"
          />
          <p className="text-xs text-muted-foreground">
            {windowDaysInput === "" && "Inherits operator default. "}
            Operator ceiling: {operatorCeiling ?? "—"} days.
            {zdrEnabled && " Zero-Data-Retention is active and supersedes this window."}
          </p>
          {windowError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {windowError}
            </p>
          )}
        </div>

        <div>
          <Button
            type="button"
            disabled={saveRetention.isPending || zdrEnabled}
            onClick={handleSaveWindow}
          >
            {saveRetention.isPending ? "Saving…" : "Save window"}
          </Button>
        </div>
      </fieldset>

      <fieldset
        className={
          zdrEnabled
            ? "flex flex-col gap-3 rounded-lg border border-border p-4 text-muted-foreground"
            : "flex flex-col gap-3 rounded-lg border border-border p-4"
        }
      >
        <legend className="px-1 text-sm font-semibold text-foreground">Effective window by store</legend>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Store</TableHead>
              <TableHead>Effective days</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {STORE_LABELS.map(({ key, label }) => (
              <TableRow key={key}>
                <TableCell>{label}</TableCell>
                <TableCell>{effective ? effective[key] : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </fieldset>

      <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
        <legend className="px-1 text-sm font-semibold text-foreground">Zero-Data-Retention (ZDR)</legend>
        <div className="flex items-center justify-between gap-4">
          <label htmlFor="zdr-enabled" className="text-sm font-medium text-foreground">
            Enable Zero-Data-Retention
          </label>
          <Switch
            id="zdr-enabled"
            aria-label="Enable Zero-Data-Retention"
            checked={zdrEnabled}
            disabled={zdrConfirmOpen}
            onCheckedChange={handleZdrToggle}
          />
        </div>
        {zdrEnabled && zdrEnabledAt && (
          <p className="text-xs text-muted-foreground">Enabled at {zdrEnabledAt}.</p>
        )}
      </fieldset>

      {/* M1-M5: third fieldset, "Data residency" — own useQuery/mutation, independent
          of Retention/ZDR above (never gates, never gated by, them). */}
      <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-4">
        <legend className="px-1 text-sm font-semibold text-foreground">Data residency</legend>

        {residencyQuery.isLoading && <Loading label="Loading residency policy" />}

        {residencyQuery.isError && !residencyQuery.isLoading && (
          <ErrorState title={getErrorTitle(residencyQuery.error)} />
        )}

        {!residencyQuery.isLoading && !residencyQuery.isError && (
          <>
            <fieldset className="flex flex-col gap-2">
              <legend className="text-sm font-medium text-foreground">Pin inference region</legend>
              <div className="flex flex-col gap-2">
                <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                  <input
                    type="radio"
                    name="residency-region"
                    checked={pendingRegion === null}
                    disabled={residencyConfirmOpen}
                    onChange={() => setPendingRegion(null)}
                    className="size-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  No pin (unrestricted)
                </label>
                {(["us", "eu", "ap"] as const).map((region) => (
                  <label
                    key={region}
                    className="flex min-h-11 items-center gap-2 text-sm text-foreground"
                  >
                    <input
                      type="radio"
                      name="residency-region"
                      checked={pendingRegion === region}
                      disabled={residencyConfirmOpen}
                      onChange={() => setPendingRegion(region)}
                      className="size-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                    {RESIDENCY_REGION_LABELS[region]}
                  </label>
                ))}
              </div>
            </fieldset>

            <div>
              <Button
                type="button"
                disabled={saveResidency.isPending || residencyConfirmOpen}
                onClick={handleResidencySave}
              >
                {saveResidency.isPending ? "Saving…" : "Save"}
              </Button>
            </div>

            {seededResidency?.region && seededResidency.updated_at && (
              <p className="text-xs text-muted-foreground">
                Pin currently: {RESIDENCY_REGION_LABELS[seededResidency.region]} — set{" "}
                {formatTimestamp(seededResidency.updated_at)}.
              </p>
            )}
          </>
        )}

        {residencyMutError && (
          <p role="alert" aria-live="polite" className="text-sm text-destructive">
            {residencyMutError}
          </p>
        )}
      </fieldset>

      {mutError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {mutError}
        </p>
      )}

      <ConfirmDialog
        open={zdrConfirmOpen}
        title="Enable Zero-Data-Retention?"
        description="Enabling ZDR immediately and permanently purges retained payload data. Disabling ZDR later does not restore purged rows — this cannot be undone."
        confirmLabel="Enable ZDR"
        onConfirm={handleConfirmZdrEnable}
        onClose={handleZdrConfirmClose}
      />

      <ConfirmDialog
        open={residencyConfirmOpen}
        title={`Pin residency to ${pendingRegion ? RESIDENCY_REGION_LABELS[pendingRegion] : ""}?`}
        description={pendingRegion ? RESIDENCY_CONSEQUENCE[pendingRegion] : undefined}
        confirmLabel={`Pin to ${pendingRegion ? RESIDENCY_REGION_LABELS[pendingRegion] : ""}`}
        onConfirm={handleConfirmResidencyPin}
        onClose={handleResidencyConfirmClose}
      />
    </div>
  );
}
