"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { getQueryClient } from "@/lib/query-client";
import { ThemeProvider } from "@/components/ui";

/**
 * Providers — the client-only context boundary (theme + react-query).
 *
 * Split out of the root layout so app/layout.tsx can stay a Server Component: the no-flash
 * <script> then renders from the server <head> with no React 19 client-head hydration warning.
 * Behavior is byte-identical to the previous client root layout — same providers, same order,
 * same defaultTheme.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient();
  return (
    <ThemeProvider defaultTheme="system">
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ThemeProvider>
  );
}
