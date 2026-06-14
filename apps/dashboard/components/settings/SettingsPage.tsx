"use client";

/**
 * SettingsPage — /settings tabbed hub (Cache · Guardrails · SSO).
 *
 * Default tab = "cache" (works for every role; non-owners don't land on a 403 SSO tab).
 * TabsContent returns null when inactive so each sub-component's useQuery only fires
 * when its tab is active — the Cache GET fires on mount (default), others on activation.
 */

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";
import { CacheSettings } from "./CacheSettings";
import { GuardrailSettings } from "./GuardrailSettings";
import { OidcSettings } from "./OidcSettings";

export function SettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage cache, guardrails, and SSO configuration for your tenant.
        </p>
      </header>

      <Tabs defaultValue="cache">
        <TabsList>
          <TabsTrigger value="cache">Cache</TabsTrigger>
          <TabsTrigger value="guardrails">Guardrails</TabsTrigger>
          <TabsTrigger value="sso">SSO</TabsTrigger>
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
      </Tabs>
    </div>
  );
}
