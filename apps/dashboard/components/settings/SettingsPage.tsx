"use client";

/**
 * SettingsPage — /settings tabbed hub (Cache · Guardrails · SSO · Provider Keys ·
 * SCIM · SAML SSO · Data & residency · Compliance).
 *
 * Default tab = "cache" (works for every role; non-owners don't land on a 403 SSO
 * tab). TabsContent returns null when inactive so each sub-component's useQuery
 * only fires when its tab is active — the Cache GET fires on mount (default),
 * others on activation.
 *
 * Tabs are CONTROLLED via the URL (`?tab=`) — copies PlatformTenantDetail.tsx's
 * exact useSearchParams/useRouter + lazy-useState-seed + "adjust state during
 * render" re-sync + handleTabChange(router.replace(...)) pattern verbatim
 * (compliance-report-center TASK.md §3 CONTRACT — FROZEN @ v1, M12). The default
 * tab is UNCHANGED ("cache") when no `?tab=` param is present — a pure regression-
 * safe addition over the prior uncontrolled `defaultValue="cache"` behavior.
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { CacheSettings } from "./CacheSettings";
import { GuardrailSettings } from "./GuardrailSettings";
import { OidcSettings } from "./OidcSettings";
import { ProviderKeysSettings } from "./ProviderKeysSettings";
import { ScimSettings } from "./ScimSettings";
import { SamlSettings } from "./SamlSettings";
import { RetentionZdrSettings } from "./RetentionZdrSettings";
import { ComplianceReportCenter } from "@/components/compliance/ComplianceReportCenter";

const TAB_VALUES = [
  "cache",
  "guardrails",
  "sso",
  "provider-keys",
  "scim",
  "saml",
  "retention",
  "compliance",
] as const;
type TabValue = (typeof TAB_VALUES)[number];

function isTabValue(v: string | null): v is TabValue {
  return v !== null && (TAB_VALUES as readonly string[]).includes(v);
}

export function SettingsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [activeTab, setActiveTab] = React.useState<TabValue>(() => {
    const fromUrl = searchParams.get("tab");
    return isTabValue(fromUrl) ? fromUrl : "cache";
  });

  // Re-sync on real navigation (browser back/forward changing ?tab=) via the
  // "adjust state during render" pattern rather than a setState-in-effect —
  // mirrors PlatformTenantDetail.tsx's own seed/reseed-from-external-source
  // convention. Compared by VALUE (.toString()), not object identity: some
  // next/navigation mocks (e.g. tests/setup.ts's legacy suite) return a fresh
  // URLSearchParams instance on every call, which would make a reference
  // comparison always "different" and loop forever (React's own
  // "Too many re-renders" guard). A string compare is correct for both real
  // Next.js (a stable instance per navigation) and any such mock.
  const searchParamsStr = searchParams.toString();
  const [prevSearchParamsStr, setPrevSearchParamsStr] = React.useState(searchParamsStr);
  if (searchParamsStr !== prevSearchParamsStr) {
    setPrevSearchParamsStr(searchParamsStr);
    const fromUrl = searchParams.get("tab");
    setActiveTab(isTabValue(fromUrl) ? fromUrl : "cache");
  }

  function handleTabChange(next: string) {
    if (!isTabValue(next)) return;
    setActiveTab(next);
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    router.replace(`?${params.toString()}`);
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        description="Manage cache, guardrails, SSO, and provider keys for your tenant."
      />

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="cache">Cache</TabsTrigger>
          <TabsTrigger value="guardrails">Guardrails</TabsTrigger>
          <TabsTrigger value="sso">SSO</TabsTrigger>
          <TabsTrigger value="provider-keys">Provider Keys</TabsTrigger>
          <TabsTrigger value="scim">SCIM</TabsTrigger>
          <TabsTrigger value="saml">SAML SSO</TabsTrigger>
          <TabsTrigger value="retention">Data & residency</TabsTrigger>
          <TabsTrigger value="compliance">Compliance</TabsTrigger>
        </TabsList>

        <TabsContent value="cache">
          <CacheSettings />
        </TabsContent>

        <TabsContent value="guardrails">
          <GuardrailSettings />
        </TabsContent>

        <TabsContent value="sso">
          <OidcSettings />
        </TabsContent>

        <TabsContent value="provider-keys">
          <ProviderKeysSettings />
        </TabsContent>

        <TabsContent value="scim">
          <ScimSettings />
        </TabsContent>

        <TabsContent value="saml">
          <SamlSettings />
        </TabsContent>

        <TabsContent value="retention">
          <RetentionZdrSettings />
        </TabsContent>

        <TabsContent value="compliance">
          <ComplianceReportCenter />
        </TabsContent>
      </Tabs>
    </div>
  );
}
