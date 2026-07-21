import { Badge } from "@/components/ui/badge";

/**
 * BaseUrlSwap — homepage-integration-proof TASK.md §3 (FROZEN @ v1).
 *
 * Mounted as the LAST child of the marketing hero's `<Reveal as="section">`
 * (see (marketing)/page.tsx), after the CTA row and before the #product
 * section boundary. A compact, static proof that Hydroa is drop-in
 * OpenAI-compatible: the real Python OpenAI SDK call, unchanged, with
 * exactly ONE added line — `base_url="{displayBaseUrl}/v1",`. No new
 * heading, no client directive, no browser-only API — a plain presentational
 * subcomponent following components/marketing/feature-card.tsx's house
 * shape (no client state, no clipboard control — see TASK.md §1 assumption).
 *
 * Both values are read from the SAME sources already shipped and tested at
 * /docs/quickstart, never re-derived here:
 *   - `baseUrl` is passed in by the page, which calls the existing
 *     publicApiBaseUrl() helper (lib/public-api-base-url.ts) — this
 *     component never reads env vars or fabricates a domain itself.
 *   - the fallback placeholder is byte-identical to QuickstartPanel.tsx's
 *     own module-private PLACEHOLDER_BASE_URL ("<configure
 *     NEXT_PUBLIC_API_BASE_URL>") — duplicated here since that constant is
 *     not exported.
 *   - the api_key literal is byte-identical to docs/quickstart/page.tsx's
 *     PLACEHOLDER_KEY ("sk-your-api-key") — never a real secret.
 *
 * Header/path shape verified against scripts/edge_smoke.sh (steps 5-6):
 * `POST {base_url}/v1/chat/completions` with `Authorization: Bearer <key>`
 * — the SDK's own `api_key` param IS that Authorization header; the
 * `base_url` shown here ends in exactly "/v1" (the SDK appends
 * "/chat/completions" itself), matching the real, human-verified call.
 */

// Byte-identical to QuickstartPanel.tsx's own module-private literal (that
// constant is not exported, so it is duplicated here per §3 CONTRACT).
const PLACEHOLDER_BASE_URL = "<configure NEXT_PUBLIC_API_BASE_URL>";
// Byte-identical to docs/quickstart/page.tsx's PLACEHOLDER_KEY.
const PLACEHOLDER_KEY = "sk-your-api-key";

export interface BaseUrlSwapProps {
  baseUrl: string | null;
}

export function BaseUrlSwap({ baseUrl }: BaseUrlSwapProps) {
  const displayBaseUrl = baseUrl ?? PLACEHOLDER_BASE_URL;

  return (
    <div
      data-testid="base-url-swap"
      className="mx-auto flex w-full max-w-xl flex-col items-center gap-3"
    >
      <Badge
        variant="secondary"
        className="border border-accent-soft-border bg-accent-soft px-3 py-1 text-accent-soft-foreground"
      >
        Drop-in proof
      </Badge>
      <pre className="w-full overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 text-left font-mono text-xs text-foreground">
        <code>
          {"from openai import OpenAI\n\nclient = OpenAI(\n"}
          <span data-testid="added-line" className="block bg-success/10 text-success-text">
            {`+   base_url="${displayBaseUrl}/v1",`}{" "}
            <span className="text-muted-foreground"># &lt;- the only line you add</span>
          </span>
          {`\n    api_key="${PLACEHOLDER_KEY}",\n)`}
        </code>
      </pre>
    </div>
  );
}
