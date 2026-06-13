"use client";

import "./globals.css";
import { Inter } from "next/font/google";
import { QueryClientProvider } from "@tanstack/react-query";
import { getQueryClient } from "@/lib/query-client";

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
    <html lang="en">
      <body className={inter.className}>
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      </body>
    </html>
  );
}
