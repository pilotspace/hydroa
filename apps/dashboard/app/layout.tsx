import "./globals.css";
import { Inter } from "next/font/google";
import { themeScript } from "@/components/ui/theme-script";
import { Providers } from "./providers";

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
