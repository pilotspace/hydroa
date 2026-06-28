"use client";

import { useEffect, useState } from "react";
import { bffGet } from "@/lib/bff-client";

type ModelsData = { data?: Array<{ id: string }> };

/**
 * Fetch the configured model ids from the control-plane catalog: GET
 * /admin/catalog/models via the BFF — the JWT-plane twin of /v1/models, which a
 * cookie session can't reach through the edge ext_authz. Returns [] on any error.
 *
 * Powers autocomplete (datalist) suggestions for free-text model fields. It is NEVER
 * a hard gate: the field stays typeable even when the catalog is empty or the fetch
 * fails — the user can always enter a model id the catalog doesn't list.
 */
export function useCatalogModels(): string[] {
  const [ids, setIds] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    bffGet<ModelsData>("/admin/catalog/models")
      .then((res) => {
        if (active) setIds(res.data?.map((m) => m.id) ?? []);
      })
      .catch(() => {
        if (active) setIds([]);
      });
    return () => {
      active = false;
    };
  }, []);
  return ids;
}

/**
 * Narrow catalog ids to a modality via `pattern`, FALLING BACK to all ids when the
 * filter is empty — so suggestions are never blank while the catalog is non-empty.
 * Because the field is free-text, an over-broad suggestion list never blocks a valid
 * entry; it only ever helps autocomplete.
 */
export function narrowModels(ids: string[], pattern: RegExp): string[] {
  const matched = ids.filter((id) => pattern.test(id));
  return matched.length > 0 ? matched : ids;
}
