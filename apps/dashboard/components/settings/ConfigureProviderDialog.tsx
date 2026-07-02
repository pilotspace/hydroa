"use client";

/**
 * ConfigureProviderDialog — provider-discriminated modal for creating/replacing a
 * tenant's BYOK credential (provider-config-ui §3 FROZEN contract).
 *
 * Signature/ownership mirror keys/CreateKeyDialog.tsx: the PARENT owns the mutation and
 * passes `onSubmit(body) => Promise<void>`; this dialog builds the provider-discriminated
 * body, calls onSubmit, and maps a BffError to an inline form error. The parent closes the
 * modal on success (which unmounts this dialog → secrets discarded).
 *
 * SECURITY: secret fields are type=password, start EMPTY, are NEVER prefilled from any GET,
 * and are KEPT on a failed save (retry) — they vanish only when the parent unmounts the
 * dialog after a successful save.
 *
 * azure mode is a radiogroup of role=radio BUTTONS (accessible name from text content) so
 * getByRole("radio") finds them while getByLabelText (fields) does not collide.
 */

import { useState, FormEvent } from "react";
import { BffError } from "@/lib/bff-client";
import { Input, Button, Switch } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";

const BEARER_PROVIDERS = new Set(["openrouter", "openai", "anthropic", "google", "minimax"]);

/** Friendly title labels (the dialog title is cosmetic; fields are what tests assert). */
const LABELS: Record<string, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  bedrock: "Bedrock",
  azure: "Azure",
  minimax: "MiniMax",
};

type AzureMode = "api_key" | "aad";

interface ConfigureProviderDialogProps {
  provider: string;
  isOpen: boolean;
  onClose: () => void;
  /** Sends the built PUT body; rejects with BffError on a backend rejection. */
  onSubmit: (body: Record<string, unknown>) => Promise<void>;
}

export function ConfigureProviderDialog({
  provider,
  isOpen,
  onClose,
  onSubmit,
}: ConfigureProviderDialogProps) {
  // bearer
  const [secret, setSecret] = useState("");
  // bedrock
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [region, setRegion] = useState("");
  const [sessionToken, setSessionToken] = useState("");
  // azure
  const [mode, setMode] = useState<AzureMode>("api_key");
  const [apiKey, setApiKey] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [apiVersion, setApiVersion] = useState("");
  // common
  const [enabled, setEnabled] = useState(true);

  const [fieldError, setFieldError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  /** Build the PUT body for this provider/mode, or null (+ set a required-field error). */
  function buildOrError(): Record<string, unknown> | null {
    if (BEARER_PROVIDERS.has(provider)) {
      if (!secret.trim()) {
        setFieldError("Secret is required");
        return null;
      }
      return { secret, enabled };
    }
    if (provider === "bedrock") {
      if (!accessKeyId.trim() || !secretAccessKey.trim() || !region.trim()) {
        setFieldError("Access key ID, secret access key, and region are required");
        return null;
      }
      return {
        access_key_id: accessKeyId,
        secret_access_key: secretAccessKey,
        region,
        ...(sessionToken.trim() ? { session_token: sessionToken } : {}),
        enabled,
      };
    }
    // azure
    if (mode === "api_key") {
      if (!apiKey.trim() || !endpoint.trim() || !apiVersion.trim()) {
        setFieldError("API key, endpoint, and API version are required");
        return null;
      }
      return { mode: "api_key", api_key: apiKey, endpoint, api_version: apiVersion, enabled };
    }
    if (
      !tenantId.trim() ||
      !clientId.trim() ||
      !clientSecret.trim() ||
      !endpoint.trim() ||
      !apiVersion.trim()
    ) {
      setFieldError("Tenant ID, client ID, client secret, endpoint, and API version are required");
      return null;
    }
    return {
      mode: "aad",
      tenant_id: tenantId,
      client_id: clientId,
      client_secret: clientSecret,
      endpoint,
      api_version: apiVersion,
      enabled,
    };
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldError(null);
    setFormError(null);
    const body = buildOrError();
    if (body === null) return; // client-side required-field block — NO request made
    setIsSubmitting(true);
    try {
      await onSubmit(body);
      // Success: the parent closes the modal (unmount) → secrets discarded.
    } catch (err) {
      // Keep all fields (incl. secrets) so the user can retry.
      setFormError(err instanceof BffError ? err.problem.title : "Failed to save credential");
    } finally {
      setIsSubmitting(false);
    }
  }

  const trapRef = useFocusTrap<HTMLDivElement>(isOpen, onClose);

  function field(
    id: string,
    label: string,
    value: string,
    setter: (v: string) => void,
    type: "text" | "password" = "text",
    placeholder = ""
  ) {
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        <Input
          id={id}
          type={type}
          value={value}
          onChange={(e) => setter(e.target.value)}
          autoComplete={type === "password" ? "new-password" : "off"}
          placeholder={placeholder}
        />
      </div>
    );
  }

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
      data-testid="configure-provider-overlay"
    >
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="configure-provider-title"
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
      >
        <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
          <h2 id="configure-provider-title" className="text-lg font-semibold text-foreground">
            Configure {LABELS[provider] ?? provider}
          </h2>

          {BEARER_PROVIDERS.has(provider) &&
            field("pk-secret", "Secret", secret, setSecret, "password", "Provider API key")}

          {provider === "bedrock" && (
            <>
              {field("pk-access-key-id", "Access key ID", accessKeyId, setAccessKeyId)}
              {field("pk-secret-access-key", "Secret access key", secretAccessKey, setSecretAccessKey, "password")}
              {field("pk-region", "Region", region, setRegion, "text", "us-east-1")}
              {field("pk-session-token", "Session token", sessionToken, setSessionToken, "password", "optional")}
            </>
          )}

          {provider === "azure" && (
            <>
              <div role="radiogroup" aria-label="Azure authentication mode" className="flex gap-2">
                <button
                  type="button"
                  role="radio"
                  aria-checked={mode === "api_key"}
                  onClick={() => setMode("api_key")}
                  className={`rounded-md border px-3 py-1.5 text-sm ${
                    mode === "api_key" ? "border-primary bg-primary/10" : "border-border"
                  }`}
                >
                  API key
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={mode === "aad"}
                  onClick={() => setMode("aad")}
                  className={`rounded-md border px-3 py-1.5 text-sm ${
                    mode === "aad" ? "border-primary bg-primary/10" : "border-border"
                  }`}
                >
                  Microsoft Entra ID (AAD)
                </button>
              </div>

              {mode === "api_key" ? (
                field("pk-api-key", "API key", apiKey, setApiKey, "password")
              ) : (
                <>
                  {field("pk-tenant-id", "Tenant ID", tenantId, setTenantId)}
                  {field("pk-client-id", "Client ID", clientId, setClientId)}
                  {field("pk-client-secret", "Client secret", clientSecret, setClientSecret, "password")}
                </>
              )}
              {field("pk-endpoint", "Endpoint", endpoint, setEndpoint, "text", "https://my-resource.openai.azure.com")}
              {field("pk-api-version", "API version", apiVersion, setApiVersion, "text", "2024-02-01")}
            </>
          )}

          {fieldError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {fieldError}
            </p>
          )}

          <div className="flex items-center justify-between gap-4">
            <label htmlFor="pk-enabled" className="text-sm font-medium text-foreground">
              Enabled
            </label>
            <Switch
              id="pk-enabled"
              aria-label="Enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>

          {formError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {formError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
