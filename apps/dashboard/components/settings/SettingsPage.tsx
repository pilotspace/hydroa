"use client";

/**
 * SettingsPage — /settings tabbed hub (Cache · Guardrails · SSO · Provider Keys).
 *
 * Default tab = "cache" (works for every role; non-owners don't land on a 403 SSO tab).
 * TabsContent returns null when inactive so each sub-component's useQuery only fires
 * when its tab is active — the Cache GET fires on mount (default), others on activation.
 */

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { CacheSettings } from "./CacheSettings";
import { GuardrailSettings } from "./GuardrailSettings";
import { OidcSettings } from "./OidcSettings";
import { ProviderKeysSettings } from "./ProviderKeysSettings";
import { ScimSettings } from "./ScimSettings";
import { SamlSettings } from "./SamlSettings";
import { RetentionZdrSettings } from "./RetentionZdrSettings";

export function SettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        description="Manage cache, guardrails, SSO, and provider keys for your tenant."
      />

      <Tabs defaultValue="cache">
        <TabsList>
          <TabsTrigger value="cache">Cache</TabsTrigger>
          <TabsTrigger value="guardrails">Guardrails</TabsTrigger>
          <TabsTrigger value="sso">SSO</TabsTrigger>
          <TabsTrigger value="provider-keys">Provider Keys</TabsTrigger>
          <TabsTrigger value="scim">SCIM</TabsTrigger>
          <TabsTrigger value="saml">SAML SSO</TabsTrigger>
          <TabsTrigger value="retention">Retention & ZDR</TabsTrigger>
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
      </Tabs>
    </div>
  );
}
