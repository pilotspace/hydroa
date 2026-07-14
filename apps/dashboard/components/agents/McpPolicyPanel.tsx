"use client";

/**
 * McpPolicyPanel — agents-console TASK.md §3 CONTRACT M9/M10/M11. Hosts TWO
 * `McpAllowListEditor` mounts (tenant scope + key scope) + the key `<select>` + the
 * inherit/custom native-radio (the SAME "pick one of a few" visual `TierSelector`/
 * `RetentionZdrSettings`'s residency picker already establish).
 *
 * Tenant: `PUT /admin/mcp-servers` is OWNER-only (mcp-connector-passthrough §3 FROZEN
 * @ v2) — a non-owner sees the SAME list read-only (`readOnly` prop), never hidden.
 * Per-key: `PUT`/`DELETE /admin/keys/{key_id}/mcp-servers` is owner/admin. Selecting
 * "Inherit tenant list" while a custom override exists fires the loosening DELETE
 * immediately (mirrors RetentionZdrSettings' residency-clear precedent: loosening is
 * safe, tightening/custom needs an explicit Save). A 422 ERR_MCP_SERVER_URL_INVALID
 * is mapped to the offending row(s) via a client-side https:// re-scan of the attempted
 * draft (the API doesn't disambiguate which row failed); ERR_MCP_SERVER_LIST_TOO_LONG
 * surfaces as a list-level error. A 404 ERR_KEY_NOT_FOUND on save (R7) replaces the
 * key editor with an inline note and refreshes the key dropdown.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { bffDelete, bffGet, bffPut, BffError } from "@/lib/bff-client";
import { McpAllowListEditor, type McpAllowListEntry } from "./McpAllowListEditor";

interface McpServersResponse {
  servers: McpAllowListEntry[];
  updated_at: string | null;
}

interface KeyMcpServersResponse {
  servers: McpAllowListEntry[] | null;
  source: "key" | "tenant";
}

const EMPTY_ENTRIES: McpAllowListEntry[] = [];
const FAIL_CLOSED_COPY =
  "Unlisted servers are refused, not warned — an agent can only reach a server on this list.";
const EMPTY_CUSTOM_COPY =
  "An empty list blocks this key from every MCP server — this is different from inheriting.";

function urlFieldErrors(entries: McpAllowListEntry[], message: string): Record<number, string> {
  const errors: Record<number, string> = {};
  entries.forEach((entry, index) => {
    if (!entry.url.startsWith("https://")) errors[index] = message;
  });
  return errors;
}

export interface McpPolicyPanelProps {
  role: string | null;
  keys: { id: string; label: string }[];
}

export function McpPolicyPanel({ role, keys }: McpPolicyPanelProps) {
  const queryClient = useQueryClient();
  const isOwner = role === "owner";

  const tenantQuery = useQuery<McpServersResponse>({
    queryKey: ["admin-mcp-servers"],
    queryFn: () => bffGet<McpServersResponse>("/admin/mcp-servers"),
    retry: false,
  });
  const [tenantFieldErrors, setTenantFieldErrors] = useState<Record<number, string>>({});
  const [tenantGeneralError, setTenantGeneralError] = useState<string | null>(null);

  async function handleTenantSave(draft: McpAllowListEntry[]) {
    setTenantFieldErrors({});
    setTenantGeneralError(null);
    try {
      const resp = await bffPut<McpServersResponse>("/admin/mcp-servers", { servers: draft });
      queryClient.setQueryData<McpServersResponse>(["admin-mcp-servers"], resp);
    } catch (err) {
      if (err instanceof BffError && err.status === 422 && err.problem.code === "ERR_MCP_SERVER_LIST_TOO_LONG") {
        setTenantGeneralError(err.problem.title);
      } else if (err instanceof BffError && err.status === 422) {
        setTenantFieldErrors(urlFieldErrors(draft, err.problem.title));
      } else if (err instanceof BffError) {
        setTenantGeneralError(err.problem.title);
      }
    }
  }

  const [selectedKeyId, setSelectedKeyId] = useState<string>("");
  const [radio, setRadio] = useState<"inherit" | "custom">("inherit");
  const [radioSeed, setRadioSeed] = useState<KeyMcpServersResponse | undefined>(undefined);
  const [keyFieldErrors, setKeyFieldErrors] = useState<Record<number, string>>({});
  const [keyGeneralError, setKeyGeneralError] = useState<string | null>(null);
  const [keyNotFound, setKeyNotFound] = useState(false);

  const keyOverrideQuery = useQuery<KeyMcpServersResponse>({
    queryKey: ["admin-key-mcp-servers", selectedKeyId],
    queryFn: () => bffGet<KeyMcpServersResponse>(`/admin/keys/${selectedKeyId}/mcp-servers`),
    enabled: selectedKeyId !== "",
    retry: false,
  });

  if (keyOverrideQuery.data && keyOverrideQuery.data !== radioSeed) {
    setRadioSeed(keyOverrideQuery.data);
    setRadio(keyOverrideQuery.data.source === "key" ? "custom" : "inherit");
  }

  function handleKeySelect(id: string) {
    setSelectedKeyId(id);
    setKeyNotFound(false);
    setKeyFieldErrors({});
    setKeyGeneralError(null);
    setRadioSeed(undefined);
  }

  async function handleKeyDeleteOverrideNow() {
    if (!selectedKeyId) return;
    try {
      await bffDelete(`/admin/keys/${selectedKeyId}/mcp-servers`);
      queryClient.setQueryData<KeyMcpServersResponse>(["admin-key-mcp-servers", selectedKeyId], {
        servers: null,
        source: "tenant",
      });
      setKeyNotFound(false);
    } catch (err) {
      if (err instanceof BffError && err.status === 404) {
        setKeyNotFound(true);
        void queryClient.invalidateQueries({ queryKey: ["admin-keys"] });
      }
    }
  }

  function handleRadioChange(next: "inherit" | "custom") {
    setRadio(next);
    if (next === "inherit" && keyOverrideQuery.data?.source === "key") {
      void handleKeyDeleteOverrideNow();
    }
  }

  async function handleKeySave(draft: McpAllowListEntry[]) {
    if (!selectedKeyId) return;
    setKeyFieldErrors({});
    setKeyGeneralError(null);
    try {
      const resp = await bffPut<KeyMcpServersResponse>(`/admin/keys/${selectedKeyId}/mcp-servers`, {
        servers: draft,
      });
      queryClient.setQueryData<KeyMcpServersResponse>(["admin-key-mcp-servers", selectedKeyId], resp);
      setKeyNotFound(false);
    } catch (err) {
      if (err instanceof BffError && err.status === 404) {
        setKeyNotFound(true);
        void queryClient.invalidateQueries({ queryKey: ["admin-keys"] });
      } else if (err instanceof BffError && err.status === 422 && err.problem.code === "ERR_MCP_SERVER_LIST_TOO_LONG") {
        setKeyGeneralError(err.problem.title);
      } else if (err instanceof BffError && err.status === 422) {
        setKeyFieldErrors(urlFieldErrors(draft, err.problem.title));
      } else if (err instanceof BffError) {
        setKeyGeneralError(err.problem.title);
      }
    }
  }

  const tenantEntries = tenantQuery.data?.servers ?? EMPTY_ENTRIES;
  const keyEntries = keyOverrideQuery.data?.servers ?? EMPTY_ENTRIES;
  const showEmptyCustomCopy =
    keyOverrideQuery.data?.source === "key" && (keyOverrideQuery.data.servers?.length ?? 0) === 0;

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-foreground">{FAIL_CLOSED_COPY}</p>

      <section aria-labelledby="mcp-tenant-heading" className="flex flex-col gap-2">
        <h3 id="mcp-tenant-heading" className="text-sm font-semibold text-foreground">
          Tenant allow-list
        </h3>
        {tenantQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : tenantQuery.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {tenantQuery.error instanceof BffError ? tenantQuery.error.problem.title : "Failed to load"}
          </p>
        ) : (
          <McpAllowListEditor
            scope="tenant"
            entries={tenantEntries}
            onSave={handleTenantSave}
            fieldErrors={tenantFieldErrors}
            readOnly={!isOwner}
          />
        )}
        {tenantGeneralError ? (
          <p role="alert" aria-live="polite" className="text-sm text-destructive">
            {tenantGeneralError}
          </p>
        ) : null}
      </section>

      <section aria-labelledby="mcp-key-heading" className="flex flex-col gap-3">
        <h3 id="mcp-key-heading" className="text-sm font-semibold text-foreground">
          Per-key override
        </h3>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="mcp-policy-key-select" className="text-xs font-medium text-muted-foreground">
            Key
          </label>
          <select
            id="mcp-policy-key-select"
            value={selectedKeyId}
            onChange={(e) => handleKeySelect(e.target.value)}
            className="h-10 w-64 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">Select a key…</option>
            {keys.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </div>

        {selectedKeyId === "" ? null : keyNotFound ? (
          <p role="alert" className="text-sm text-destructive">
            This key no longer exists.
          </p>
        ) : keyOverrideQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <>
            <fieldset className="flex flex-col gap-2">
              <legend className="text-sm font-medium text-foreground">Servers reachable for this key</legend>
              <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                <input
                  type="radio"
                  name="mcp-key-scope"
                  checked={radio === "inherit"}
                  onChange={() => handleRadioChange("inherit")}
                  className="size-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                Inherit tenant list
              </label>
              <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                <input
                  type="radio"
                  name="mcp-key-scope"
                  checked={radio === "custom"}
                  onChange={() => handleRadioChange("custom")}
                  className="size-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                Custom list for this key
              </label>
            </fieldset>

            {radio === "custom" ? (
              <>
                {showEmptyCustomCopy ? <p className="text-xs text-muted-foreground">{EMPTY_CUSTOM_COPY}</p> : null}
                <McpAllowListEditor
                  scope="key"
                  entries={keyEntries}
                  onSave={handleKeySave}
                  fieldErrors={keyFieldErrors}
                />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                Inheriting the tenant allow-list ({tenantEntries.length} server
                {tenantEntries.length === 1 ? "" : "s"}).
              </p>
            )}
          </>
        )}

        {keyGeneralError ? (
          <p role="alert" aria-live="polite" className="text-sm text-destructive">
            {keyGeneralError}
          </p>
        ) : null}
      </section>
    </div>
  );
}
