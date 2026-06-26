import "./globals.css";
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { themeScript } from "@/components/ui/theme-script";
import { Providers } from "./providers";

const SITE_DESCRIPTION =
  "Hydroa is the enterprise AI proxy: multi-provider routing, per-tenant cost tracking, key governance, and rate limiting behind one OpenAI-compatible endpoint.";

// Site-wide metadata defaults (v50 harden-marketing). Pages MERGE over these:
// the title `template` wraps each page's string title; metadataBase resolves the
// per-page canonical + OG URLs. NEXT_PUBLIC_SITE_URL overrides the origin at deploy.
export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://app.hydroa.dev"),
  title: {
    default: "Hydroa — AI Proxy for Enterprise Teams",
    template: "%s · Hydroa",
  },
  description: SITE_DESCRIPTION,
  applicationName: "Hydroa",
  openGraph: {
    type: "website",
    siteName: "Hydroa",
    title: "Hydroa — AI Proxy for Enterprise Teams",
    description: SITE_DESCRIPTION,
  },
  twitter: { card: "summary_large_image" },
  robots: { index: true, follow: true },
};

// Inter — the design-system base typeface (self-hosted via next/font), applied to
// the document body so every surface inherits it. Falls back to the token stack
// (system-ui, sans-serif) defined in globals.css.
const inter = Inter({ subsets: ["latin"], display: "swap" });

// Server Component (no "use client"): the no-flash theme <script> renders from the server <head>
// (no React 19 client-head hydration warning). The client context (theme + react-query) lives in
// the Providers wrapper around {children}.
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* No-flash theme: apply class="dark" before first paint from the stored choice.
            Rendered as the script element's text child (code-controlled, no raw-HTML API). */}
        <script>{themeScript()}</script>
      </head>
      <body className={inter.className}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
