import "./globals.css";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
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

// Geist + Geist Mono — the design-system typefaces (self-hosted via next/font),
// exposed as CSS variables so the token layer (globals.css) can reference them:
// --font-sans → var(--font-geist-sans), --font-mono → var(--font-geist-mono).
// Geist is the anti-slop, off-Inter technical grotesque for UI; Geist Mono carries
// every metric/numeral (tabular figures). Both fall back to the system stacks in
// globals.css. (dashboard-hallmark-restyle — "Airier" direction, Tin-locked 2026-07-17.)
const geistSans = Geist({ subsets: ["latin"], display: "swap", variable: "--font-geist-sans" });
const geistMono = Geist_Mono({ subsets: ["latin"], display: "swap", variable: "--font-geist-mono" });

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
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
