"use client";

/**
 * components/chat/MessageMarkdown.tsx — chat full-mockup-parity.
 *
 * Renders assistant chat content as sanitized, GitHub-flavored Markdown with
 * syntax-highlighted fenced code blocks (Copy on hover) — the design prototype's
 * `markdown: true, code: true`. Raw HTML is NEVER rendered: react-markdown does
 * not parse embedded HTML unless rehype-raw is added (it is not), so model output
 * is XSS-safe by construction. Highlight classes come from rehype-highlight; the
 * theme CSS is imported once in app/globals.css.
 */

import * as React from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/cn";

function CopyCodeButton({ getText }: { getText: () => string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : "Copy code"}
      onClick={() => {
        const text = getText();
        if (!text || !navigator.clipboard) return;
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => {},
        );
      }}
      className="inline-flex items-center gap-1 rounded-md border border-border bg-background/80 px-2 py-1 text-xs font-medium text-muted-foreground backdrop-blur transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {copied ? <Check className="size-3" aria-hidden="true" /> : <Copy className="size-3" aria-hidden="true" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function CodeBlock({ children }: { children?: React.ReactNode }) {
  const ref = React.useRef<HTMLPreElement>(null);
  return (
    <div className="group relative my-2 overflow-hidden rounded-lg border border-border bg-muted/40">
      <div className="absolute right-2 top-2 z-10 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <CopyCodeButton getText={() => ref.current?.textContent ?? ""} />
      </div>
      <pre ref={ref} className="overflow-x-auto p-3 text-xs leading-relaxed">
        {children}
      </pre>
    </div>
  );
}

const COMPONENTS: Components = {
  // Fenced code: react-markdown emits <pre><code>; wrap in a Copy-affordance block.
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  code: ({ className, children }) => {
    const isBlock = typeof className === "string" && className.includes("language-");
    if (isBlock) {
      return <code className={className}>{children}</code>;
    }
    return (
      <code className="rounded bg-foreground/10 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
    );
  },
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium underline underline-offset-2"
    >
      {children}
    </a>
  ),
};

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeHighlight];

/**
 * Render `content` (assistant Markdown). Element spacing/typography is applied via
 * scoped utilities so the bubble stays compact and on-token.
 */
export function MessageMarkdown({ content, className }: { content: string; className?: string }) {
  return (
    <div
      className={cn(
        "text-sm leading-relaxed",
        "[&_p]:my-1.5 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5",
        "[&_h1]:mb-1 [&_h1]:mt-2 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-1 [&_h2]:mt-2 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:font-semibold",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
        "[&_table]:my-2 [&_table]:w-full [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1",
        className,
      )}
    >
      <Markdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={COMPONENTS}>
        {content}
      </Markdown>
    </div>
  );
}
