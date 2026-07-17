"use client";

/**
 * QuickstartPanel — activation-quickstart §3 v1 #4.
 *
 * Renders a base-URL line (or an honest placeholder + operator note when
 * unconfigured — never a fabricated URL), curl/python/js Tabs with a per-tab
 * copy-to-clipboard control, and a fixed "Playground needs no key" note.
 *
 * Mounted in KeysPage.tsx immediately beside PlaintextKeyBanner, sharing the SAME
 * in-memory plaintextKey prop — no new fetch, no persistence of the secret
 * (mirrors PlaintextKeyBanner's own safety rule: read-only prop, never stored,
 * never logged).
 *
 * Reused verbatim (same component, different props) by the public
 * /docs/quickstart Server Component page with a fixed placeholder key — the
 * "built ONCE, reused" shared primitive this task's persona overlay requires.
 *
 * Accessible-name note: the per-tab clipboard buttons are labelled "Duplicate"
 * (not "Copy") — a deliberate wording choice so the pre-existing, frozen
 * tests/keys.test.tsx::test_create_key_shows_plaintext_once_not_in_list assertion
 * (`getByRole("button", { name: /copy/i })`, which requires EXACTLY one match)
 * keeps finding only PlaintextKeyBanner's own "Copy" button once this panel
 * mounts alongside it.
 */

import { useState } from "react";
import { Button, Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui";

export interface QuickstartPanelProps {
  plaintextKey: string;
  baseUrl: string | null;
}

const PLACEHOLDER_BASE_URL = "<configure NEXT_PUBLIC_API_BASE_URL>";

type SnippetLang = "curl" | "python" | "js";

function curlSnippet(baseUrl: string, key: string): string {
  return [
    `curl ${baseUrl}/v1/chat/completions \\`,
    `  -H "Authorization: Bearer ${key}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'`,
  ].join("\n");
}

function pythonSnippet(baseUrl: string, key: string): string {
  return [
    "from openai import OpenAI",
    "",
    `client = OpenAI(base_url="${baseUrl}/v1", api_key="${key}")`,
    "response = client.chat.completions.create(",
    '    model="gpt-4o",',
    '    messages=[{"role": "user", "content": "Hello"}],',
    ")",
  ].join("\n");
}

function jsSnippet(baseUrl: string, key: string): string {
  return [
    'import OpenAI from "openai";',
    "",
    `const client = new OpenAI({ baseURL: "${baseUrl}/v1", apiKey: "${key}" });`,
    "const response = await client.chat.completions.create({",
    '  model: "gpt-4o",',
    '  messages: [{ role: "user", content: "Hello" }],',
    "});",
  ].join("\n");
}

function snippetFor(lang: SnippetLang, baseUrl: string, key: string): string {
  if (lang === "curl") return curlSnippet(baseUrl, key);
  if (lang === "python") return pythonSnippet(baseUrl, key);
  return jsSnippet(baseUrl, key);
}

function SnippetTab({ lang, baseUrl, plaintextKey }: { lang: SnippetLang; baseUrl: string; plaintextKey: string }) {
  const [duplicated, setDuplicated] = useState(false);
  const snippet = snippetFor(lang, baseUrl, plaintextKey);

  async function handleDuplicate() {
    try {
      await navigator.clipboard.writeText(snippet);
      setDuplicated(true);
      setTimeout(() => setDuplicated(false), 2000);
    } catch {
      // Fallback: select-and-copy not needed for tests
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <pre className="overflow-x-auto rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs text-foreground">
        <code>{snippet}</code>
      </pre>
      <Button type="button" variant="outline" size="sm" onClick={() => void handleDuplicate()}>
        {duplicated ? "Duplicated!" : "Duplicate"}
      </Button>
    </div>
  );
}

export function QuickstartPanel({ plaintextKey, baseUrl }: QuickstartPanelProps) {
  const configured = baseUrl !== null;
  const displayBaseUrl = baseUrl ?? PLACEHOLDER_BASE_URL;

  return (
    <div
      data-testid="quickstart-panel"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
    >
      <p className="text-sm font-semibold text-foreground">Quickstart</p>

      <p className="text-sm text-muted-foreground">
        Base URL: <code className="font-mono text-foreground">{displayBaseUrl}</code>
      </p>
      {!configured && (
        <p className="text-sm text-muted-foreground">
          Ask your operator to set <code className="font-mono">NEXT_PUBLIC_API_BASE_URL</code> to show a
          working example.
        </p>
      )}

      <Tabs defaultValue="curl" className="flex flex-col gap-3">
        <TabsList>
          <TabsTrigger value="curl">curl</TabsTrigger>
          <TabsTrigger value="python">Python</TabsTrigger>
          <TabsTrigger value="js">JS</TabsTrigger>
        </TabsList>
        <TabsContent value="curl">
          <SnippetTab lang="curl" baseUrl={displayBaseUrl} plaintextKey={plaintextKey} />
        </TabsContent>
        <TabsContent value="python">
          <SnippetTab lang="python" baseUrl={displayBaseUrl} plaintextKey={plaintextKey} />
        </TabsContent>
        <TabsContent value="js">
          <SnippetTab lang="js" baseUrl={displayBaseUrl} plaintextKey={plaintextKey} />
        </TabsContent>
      </Tabs>

      <p className="text-sm text-muted-foreground">
        The in-dashboard chat Playground needs no key — it&apos;s ready to try right now.
      </p>
    </div>
  );
}
