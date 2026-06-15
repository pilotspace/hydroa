"use client";

/**
 * OidcSettings — SSO/OIDC tab content for the /settings hub (OWNER-only).
 *
 * GET /admin/oidc → OidcConf | 404 (unconfigured → empty form) | 403 (admin/member → ErrorState)
 * PUT /admin/oidc → saves config; client_secret is WRITE-ONLY:
 *   - The GET always returns client_secret:"<stored>" (a sentinel, not the real secret).
 *   - The password input starts EMPTY and is NEVER prefilled from the GET response.
 *   - Save is BLOCKED client-side if the secret field is empty (no request made).
 *   - A note "A client secret is stored" is shown when a config exists.
 * SECURITY: the "<stored>" sentinel never enters any input value or state.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Switch, Button, Input, Loading, ErrorState } from "@/components/ui";

interface OidcConf {
  tenant_id: string;
  issuer: string;
  client_id: string;
  /** Always "<stored>" from GET — never put into the password input */
  client_secret: string;
  authorize_url: string;
  token_url: string;
  jwks_url: string;
  email_domains: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface OidcPut {
  issuer: string;
  client_id: string;
  client_secret: string;
  token_url: string;
  jwks_url: string;
  email_domains: string[];
  authorize_url?: string;
  enabled?: boolean;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function OidcSettings() {
  const queryClient = useQueryClient();

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery<OidcConf>({
    queryKey: ["admin-oidc"],
    queryFn: () => bffGet<OidcConf>("/admin/oidc"),
    // 404 → treat as "unconfigured" (not an error); 403 → real error
    retry: false,
  });

  // 404 means "not configured yet" — show an empty editable form
  const is404 = isError && error instanceof BffError && error.status === 404;
  // 403 means "owner role required" — show ErrorState, no form
  const is403 = isError && error instanceof BffError && error.status === 403;
  // Other GET errors (500 etc)
  const isOtherError = isError && !is404 && !is403;

  // Whether an existing config is present (200 response)
  const hasConfig = !!data;

  // Form state — never prefill client_secret
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const [tokenUrl, setTokenUrl] = useState("");
  const [jwksUrl, setJwksUrl] = useState("");
  const [emailDomains, setEmailDomains] = useState("");
  const [oidcEnabled, setOidcEnabled] = useState(false);
  // SECURITY: this starts empty and is NEVER populated from the GET response
  const [clientSecret, setClientSecret] = useState("");

  const [secretError, setSecretError] = useState<string | null>(null);
  const [mutError, setMutError] = useState<string | null>(null);

  // Populate form from GET data (except client_secret — never prefilled) via
  // React's "adjust state during render" guard (no setState in an effect): fires
  // only when the query data reference changes (initial load + after a save
  // reinstalls it via setQueryData), so it converges in one render and never loops.
  const [seededData, setSeededData] = useState<OidcConf | undefined>(undefined);
  if (data && data !== seededData) {
    setSeededData(data);
    setIssuer(data.issuer);
    setClientId(data.client_id);
    setAuthorizeUrl(data.authorize_url ?? "");
    setTokenUrl(data.token_url);
    setJwksUrl(data.jwks_url);
    setEmailDomains(data.email_domains.join(", "));
    setOidcEnabled(data.enabled);
    // SECURITY: client_secret intentionally NOT set from data.client_secret
  }

  const saveOidc = useMutation({
    mutationFn: (body: OidcPut) => bffPut<OidcConf>("/admin/oidc", body),
    onSuccess: (resp) => {
      setMutError(null);
      setSecretError(null);
      // Clear the secret input after successful save (write-only UX)
      setClientSecret("");
      queryClient.setQueryData<OidcConf>(["admin-oidc"], resp);
    },
    onError: (err) => {
      setMutError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  function handleSave() {
    setMutError(null);
    setSecretError(null);

    // SECURITY: block save if secret is empty — required on every save
    if (!clientSecret.trim()) {
      setSecretError("Client secret is required");
      return;
    }

    const emailDomainsArr = emailDomains
      .split(",")
      .map((d) => d.trim())
      .filter(Boolean);

    saveOidc.mutate({
      issuer,
      client_id: clientId,
      client_secret: clientSecret,
      authorize_url: authorizeUrl,
      token_url: tokenUrl,
      jwks_url: jwksUrl,
      email_domains: emailDomainsArr,
      enabled: oidcEnabled,
    });
  }

  if (isLoading) {
    return <Loading label="Loading SSO settings" />;
  }

  // 403: owner-only, admin/member sees an error state with no form
  if (is403) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  // Other GET errors (500 etc) show error state
  if (isOtherError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  // 404 or 200: render the form (404 → empty, 200 → prefilled except secret)
  return (
    <div className="flex flex-col gap-6">
      {hasConfig && (
        <p className="text-sm text-muted-foreground">
          A client secret is stored. Re-enter it below to save changes.
        </p>
      )}

      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-issuer" className="text-sm font-medium text-foreground">
            Issuer
          </label>
          <Input
            id="oidc-issuer"
            type="url"
            value={issuer}
            onChange={(e) => setIssuer(e.target.value)}
            placeholder="https://idp.example.com"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-client-id" className="text-sm font-medium text-foreground">
            Client ID
          </label>
          <Input
            id="oidc-client-id"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="client-id"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-client-secret" className="text-sm font-medium text-foreground">
            Client secret
          </label>
          {/* SECURITY: type=password, starts empty, never prefilled from GET */}
          <Input
            id="oidc-client-secret"
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder="Re-enter to save"
            autoComplete="new-password"
          />
          {secretError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {secretError}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-authorize-url" className="text-sm font-medium text-foreground">
            Authorize URL
          </label>
          <Input
            id="oidc-authorize-url"
            type="url"
            value={authorizeUrl}
            onChange={(e) => setAuthorizeUrl(e.target.value)}
            placeholder="https://idp.example.com/auth"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-token-url" className="text-sm font-medium text-foreground">
            Token URL
          </label>
          <Input
            id="oidc-token-url"
            type="url"
            value={tokenUrl}
            onChange={(e) => setTokenUrl(e.target.value)}
            placeholder="https://idp.example.com/token"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-jwks-url" className="text-sm font-medium text-foreground">
            JWKS URL
          </label>
          <Input
            id="oidc-jwks-url"
            type="url"
            value={jwksUrl}
            onChange={(e) => setJwksUrl(e.target.value)}
            placeholder="https://idp.example.com/jwks"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="oidc-email-domains" className="text-sm font-medium text-foreground">
            Email domains
          </label>
          <Input
            id="oidc-email-domains"
            value={emailDomains}
            onChange={(e) => setEmailDomains(e.target.value)}
            placeholder="acme.io, example.com"
          />
          <p className="text-xs text-muted-foreground">Comma-separated list of allowed email domains.</p>
        </div>

        <div className="flex items-center justify-between gap-4">
          <label htmlFor="oidc-enabled" className="text-sm font-medium text-foreground">
            Enable SSO
          </label>
          <Switch
            id="oidc-enabled"
            aria-label="Enable SSO"
            checked={oidcEnabled}
            onCheckedChange={setOidcEnabled}
          />
        </div>
      </div>

      {mutError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {mutError}
        </p>
      )}

      <div>
        <Button
          type="button"
          disabled={saveOidc.isPending}
          onClick={handleSave}
        >
          {saveOidc.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
