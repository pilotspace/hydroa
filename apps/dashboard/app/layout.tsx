"use client";

import "./globals.css";
import { Inter } from "next/font/google";
import { QueryClientProvider } from "@tanstack/react-query";
import { getQueryClient } from "@/lib/query-client";
import { ThemeProvider, themeScript } from "@/components/ui";

// Inter — the design-system base typeface (self-hosted via next/font), applied to
// the document body so every surface inherits it. Falls back to the token stack
// (system-ui, sans-serif) defined in globals.css.
const inter = Inter({ subsets: ["latin"], display: "swap" });

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const queryClient = getQueryClient();
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* No-flash theme: apply class="dark" before first paint from the stored choice.
            Rendered as the script element's text child (code-controlled, no raw-HTML API). */}
        <script>{themeScript()}</script>
      </head>
      <body className={inter.className}>
        <ThemeProvider defaultTheme="system">
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
