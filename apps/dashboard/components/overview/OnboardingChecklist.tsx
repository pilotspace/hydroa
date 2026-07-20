"use client";

/**
 * OnboardingChecklist — activation-quickstart §3 v1 #6.
 *
 * Self-fetching, dismissible, role-aware 4-step activation card:
 *   create_key  <- GET /admin/keys            length > 0        (every role)
 *   first_call  <- GET /admin/usage           total_requests>0  (every role)
 *   byok        <- GET /admin/provider-keys   keys.length > 0   (enabled only role==="owner")
 *   invite      <- GET /admin/invites         invites.length>0  (enabled only role in {"owner","admin"})
 *
 * A step whose query never fires (role lacks permission) is simply not shown or
 * counted — never queried, never a 403 round trip.
 *
 * Dismiss persists per-tenant in localStorage, read/written ONLY inside a
 * useEffect (never a lazy useState initializer — the shipped SSR-safety
 * convention this task's frontend-engineer persona names). First paint always
 * shows the default (visible) state; the dismissed state applies on the client
 * immediately after mount.
 *
 * Auto-hides permanently, independent of manual dismissal, once every step
 * VISIBLE to the current role is complete.
 *
 * Mounted in OverviewPage.tsx above the existing KPI section.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { bffGet } from "@/lib/bff-client";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { Button } from "@/components/ui";

interface ApiKeySummary {
  key_id: string;
}
interface UsageSummary {
  total_requests: number;
}
interface ProviderKeysResponse {
  keys: unknown[];
}
interface InvitesResponse {
  invites: unknown[];
}
// ADDITIVE (member-verified-code-entry §3, FROZEN): only the fields this step's
// completion rule needs — the console's own DomainClaimListItem is the full shape.
interface DomainClaimCheckItem {
  status: string;
  member_verified_at: string | null;
}
interface DomainClaimsResponse {
  claims: DomainClaimCheckItem[];
}

interface Step {
  id: "create_key" | "first_call" | "byok" | "invite" | "confirm_domain";
  label: string;
  complete: boolean;
  // ADDITIVE (member-verified-code-entry §3, FROZEN): a deep-link target — a step
  // without one renders as plain text, unchanged from every existing step.
  href?: string;
}

function dismissKey(tenantId: string): string {
  return `hydroa_onboarding_dismissed_${tenantId}`;
}

export function OnboardingChecklist() {
  const { data: currentUser, isLoading: userLoading } = useCurrentUser();
  const role = currentUser?.role ?? null;
  const tenantId = currentUser?.tenant_id ?? null;

  const [dismissed, setDismissed] = useState(false);

  // SSR-safe: localStorage is read ONLY inside this effect, never a lazy
  // useState initializer — first render (server + client hydration) always
  // shows the default (not-dismissed) state; the client-only truth applies
  // right after mount, with no hydration-mismatch warning.
  useEffect(() => {
    if (!tenantId) return;
    // One-shot sync from an external store (localStorage) — the SSR-safe pattern
    // for a browser-only seed; not a cascading render. (rule is over-strict here,
    // mirrors LoginForm.tsx's identical SSO-domain-seed precedent.)
    const stored = localStorage.getItem(dismissKey(tenantId));
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored === "1") setDismissed(true);
  }, [tenantId]);

  const keysQuery = useQuery<ApiKeySummary[]>({
    queryKey: ["onboarding-keys"],
    queryFn: () => bffGet<ApiKeySummary[]>("/admin/keys"),
    enabled: !!currentUser,
  });
  const usageQuery = useQuery<UsageSummary>({
    queryKey: ["onboarding-usage"],
    queryFn: () => bffGet<UsageSummary>("/admin/usage"),
    enabled: !!currentUser,
  });
  const providerKeysQuery = useQuery<ProviderKeysResponse>({
    queryKey: ["onboarding-provider-keys"],
    queryFn: () => bffGet<ProviderKeysResponse>("/admin/provider-keys"),
    enabled: role === "owner",
  });
  const invitesQuery = useQuery<InvitesResponse>({
    queryKey: ["onboarding-invites"],
    queryFn: () => bffGet<InvitesResponse>("/admin/invites"),
    enabled: role === "owner" || role === "admin",
  });
  // ADDITIVE (member-verified-code-entry §3 item 7, FROZEN): "claims query enabled
  // only for owner" — role-gated identically to byok, never issued for a non-owner.
  const domainClaimsQuery = useQuery<DomainClaimsResponse>({
    queryKey: ["onboarding-domain-claims"],
    queryFn: () => bffGet<DomainClaimsResponse>("/admin/domain-claims"),
    enabled: role === "owner",
  });

  if (userLoading || !currentUser || !tenantId) return null;

  const byokVisible = role === "owner";
  const inviteVisible = role === "owner" || role === "admin";
  const confirmDomainVisible = role === "owner";

  // Wait for every VISIBLE-and-enabled query before rendering anything — an
  // honest degrade (never a wrong/half-known checklist), matching the
  // "honest, never fabricate" convention elsewhere on this page.
  if (keysQuery.isLoading || usageQuery.isLoading) return null;
  if (byokVisible && providerKeysQuery.isLoading) return null;
  if (inviteVisible && invitesQuery.isLoading) return null;
  if (confirmDomainVisible && domainClaimsQuery.isLoading) return null;
  if (keysQuery.isError || usageQuery.isError) return null;
  if (byokVisible && providerKeysQuery.isError) return null;
  if (inviteVisible && invitesQuery.isError) return null;
  if (confirmDomainVisible && domainClaimsQuery.isError) return null;

  const steps: Step[] = [
    {
      id: "create_key",
      label: "Create your first API key",
      complete: (keysQuery.data?.length ?? 0) > 0,
    },
    {
      id: "first_call",
      label: "Make your first call",
      complete: (usageQuery.data?.total_requests ?? 0) > 0,
    },
  ];
  if (byokVisible) {
    steps.push({
      id: "byok",
      label: "Add a provider key (BYOK)",
      complete: (providerKeysQuery.data?.keys.length ?? 0) > 0,
    });
  }
  if (inviteVisible) {
    steps.push({
      id: "invite",
      label: "Invite a teammate",
      complete: (invitesQuery.data?.invites.length ?? 0) > 0,
    });
  }
  if (confirmDomainVisible) {
    steps.push({
      id: "confirm_domain",
      label: "Confirm your work email domain",
      href: "/app/settings?tab=domains",
      complete: (domainClaimsQuery.data?.claims ?? []).some(
        (c) => c.member_verified_at != null || c.status === "verified",
      ),
    });
  }

  const allVisibleComplete = steps.every((s) => s.complete);
  const visible = !dismissed && !allVisibleComplete;
  if (!visible) return null;

  function handleDismiss() {
    if (tenantId) localStorage.setItem(dismissKey(tenantId), "1");
    setDismissed(true);
  }

  return (
    <section
      data-testid="onboarding-checklist"
      aria-labelledby="onboarding-checklist-heading"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="onboarding-checklist-heading" className="text-base font-semibold text-foreground">
          Get started with Hydroa
        </h2>
        <Button type="button" variant="ghost" size="sm" onClick={handleDismiss}>
          Dismiss
        </Button>
      </div>
      <ul className="flex flex-col gap-2">
        {steps.map((step) => (
          <li
            key={step.id}
            data-testid={`step-${step.id}`}
            data-complete={step.complete ? "true" : "false"}
            className="flex items-center gap-2 text-sm text-foreground"
          >
            <span
              aria-hidden="true"
              className={
                step.complete
                  ? "flex size-5 items-center justify-center rounded-full bg-success/20 text-success"
                  : "flex size-5 items-center justify-center rounded-full border border-border text-muted-foreground"
              }
            >
              {step.complete ? "✓" : ""}
            </span>
            {step.href ? (
              <Link href={step.href} className="font-medium text-accent-soft-foreground underline underline-offset-2">
                {step.label}
              </Link>
            ) : (
              <span>{step.label}</span>
            )}
            <span className="sr-only">{step.complete ? " — complete" : " — incomplete"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
