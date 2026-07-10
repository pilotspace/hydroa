"use client";

/**
 * SamlSettings — SAML SSO tab content for the /settings hub (OWNER-only).
 *
 * GET /admin/saml → SamlConf | 404 ERR_SAML_CONFIG_NOT_FOUND (unconfigured → empty,
 *                    fully-editable form, never an error) | 403
 *                    ERR_AUTH_FORBIDDEN_OWNER_REQUIRED (ErrorState, no form)
 * PUT /admin/saml → saves config. M6 — a deliberate divergence from OidcSettings's
 * client_secret (write-only, never prefilled): idp_x509_cert IS public key material
 * (frozen contract: "NOT a secret... returned in full on GET") — the cert Textarea IS
 * prefilled from GET and resubmits unchanged unless the admin edits it.
 * sp_entity_id/acs_url are ALWAYS server-derived, shown read-only in copyable <code>
 * blocks — only once a config exists (nothing to derive on the first-time empty path).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Switch, Button, Input, Textarea, Loading, ErrorState } from "@/components/ui";

interface SamlConf {
  tenant_id: string;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  sp_entity_id: string;
  acs_url: string;
  email_domains: string[];
  email_attribute_name: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

interface SamlPut {
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  email_domains: string[];
  email_attribute_name?: string;
  enabled?: boolean;
}

/**
 * A 422 from PUT /admin/saml is EITHER a normal problem+json (a `title`, e.g. the
 * ERR_SAML_CERT_INVALID case) OR the raw FastAPI validation envelope
 * ({ detail: [{ type, loc, msg, input }] } — the SSRF/bad-URL case, returned directly
 * via JSONResponse in saml_admin_router.py, bypassing the ProblemError wrapper).
 * Surface whichever shape came back rather than assuming one.
 */
function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) {
    const problem = err.problem as { title?: string; detail?: unknown };
    if (problem.title) return problem.title;
    if (Array.isArray(problem.detail) && problem.detail.length > 0) {
      const first = problem.detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
    return "An error occurred";
  }
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function SamlSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery<SamlConf>({
    queryKey: ["admin-saml"],
    queryFn: () => bffGet<SamlConf>("/admin/saml"),
    retry: false,
  });

  // 404 means "not configured yet" — show an empty editable form (never an error).
  const is404 = isError && error instanceof BffError && error.status === 404;
  // 403 means "owner role required" — show ErrorState, no form.
  const is403 = isError && error instanceof BffError && error.status === 403;
  const isOtherError = isError && !is404 && !is403;

  const hasConfig = !!data;

  const [idpEntityId, setIdpEntityId] = useState("");
  const [idpSsoUrl, setIdpSsoUrl] = useState("");
  // M6: cert IS seeded from GET below — unlike OidcSettings's write-only client_secret.
  const [idpCert, setIdpCert] = useState("");
  const [emailDomains, setEmailDomains] = useState("");
  const [emailAttributeName, setEmailAttributeName] = useState("");
  const [samlEnabled, setSamlEnabled] = useState(true);

  const [mutError, setMutError] = useState<string | null>(null);

  // Seed/reseed local editable state from server data via React's "adjust state
  // during render" guard (no setState in an effect) — mirrors OidcSettings/
  // GuardrailSettings verbatim: fires only when the query data reference changes, so
  // it converges in one render and never loops.
  const [seededData, setSeededData] = useState<SamlConf | undefined>(undefined);
  if (data && data !== seededData) {
    setSeededData(data);
    setIdpEntityId(data.idp_entity_id);
    setIdpSsoUrl(data.idp_sso_url);
    setIdpCert(data.idp_x509_cert);
    setEmailDomains(data.email_domains.join(", "));
    setEmailAttributeName(data.email_attribute_name ?? "");
    setSamlEnabled(data.enabled);
  }

  const saveSaml = useMutation({
    mutationFn: (body: SamlPut) => bffPut<SamlConf>("/admin/saml", body),
    onSuccess: (resp) => {
      setMutError(null);
      queryClient.setQueryData<SamlConf>(["admin-saml"], resp);
    },
    onError: (err) => {
      setMutError(getErrorTitle(err));
    },
  });

  function handleSave() {
    setMutError(null);

    const emailDomainsArr = emailDomains
      .split(",")
      .map((d) => d.trim())
      .filter(Boolean);

    saveSaml.mutate({
      idp_entity_id: idpEntityId,
      idp_sso_url: idpSsoUrl,
      idp_x509_cert: idpCert,
      email_domains: emailDomainsArr,
      email_attribute_name: emailAttributeName || undefined,
      enabled: samlEnabled,
    });
  }

  if (isLoading) {
    return <Loading label="Loading SAML SSO settings" />;
  }

  // 403: owner-only, non-owner sees an error state with no form.
  if (is403) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  // Other GET errors (500, cross-tenant 404 as ERR_TENANT_NOT_FOUND, etc.) show error state.
  if (isOtherError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  // 404 or 200: render the form (404 → empty, 200 → prefilled, including the cert).
  return (
    <div className="flex flex-col gap-6">
      {hasConfig && (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 p-4">
          <p className="text-sm text-muted-foreground">
            Give these values to your identity provider when configuring the SAML app.
          </p>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">SP Entity ID</span>
            <code className="block break-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">
              {data?.sp_entity_id}
            </code>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">ACS URL</span>
            <code className="block break-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">
              {data?.acs_url}
            </code>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="saml-idp-entity-id" className="text-sm font-medium text-foreground">
            IdP Entity ID
          </label>
          <Input
            id="saml-idp-entity-id"
            value={idpEntityId}
            onChange={(e) => setIdpEntityId(e.target.value)}
            placeholder="https://idp.example.com/entity"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="saml-idp-sso-url" className="text-sm font-medium text-foreground">
            IdP SSO URL
          </label>
          <Input
            id="saml-idp-sso-url"
            type="url"
            value={idpSsoUrl}
            onChange={(e) => setIdpSsoUrl(e.target.value)}
            placeholder="https://idp.example.com/sso"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="saml-idp-cert" className="text-sm font-medium text-foreground">
            IdP x509 certificate
          </label>
          {/* M6: prefilled from GET — the cert is public key material, not a secret. */}
          <Textarea
            id="saml-idp-cert"
            value={idpCert}
            onChange={(e) => setIdpCert(e.target.value)}
            placeholder="-----BEGIN CERTIFICATE-----"
            rows={8}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="saml-email-domains" className="text-sm font-medium text-foreground">
            Email domains
          </label>
          <Input
            id="saml-email-domains"
            value={emailDomains}
            onChange={(e) => setEmailDomains(e.target.value)}
            placeholder="acme.io, example.com"
          />
          <p className="text-xs text-muted-foreground">Comma-separated list of allowed email domains.</p>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="saml-email-attribute" className="text-sm font-medium text-foreground">
            Email attribute name
          </label>
          <Input
            id="saml-email-attribute"
            value={emailAttributeName}
            onChange={(e) => setEmailAttributeName(e.target.value)}
            placeholder="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
          />
        </div>

        <div className="flex items-center justify-between gap-4">
          <label htmlFor="saml-enabled" className="text-sm font-medium text-foreground">
            Enable SAML SSO
          </label>
          <Switch
            id="saml-enabled"
            aria-label="Enable SAML SSO"
            checked={samlEnabled}
            onCheckedChange={setSamlEnabled}
          />
        </div>
      </div>

      {mutError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {mutError}
        </p>
      )}

      <div>
        <Button type="button" disabled={saveSaml.isPending} onClick={handleSave}>
          {saveSaml.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
