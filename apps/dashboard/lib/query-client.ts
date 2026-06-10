/**
 * lib/query-client.ts — TanStack Query client singleton
 *
 * A single QueryClient instance for the app. Tests create their own
 * per-test clients via makeQueryClient() to avoid cross-test cache pollution.
 */

import { QueryClient } from "@tanstack/react-query";

let _client: QueryClient | null = null;

export function getQueryClient(): QueryClient {
  if (!_client) {
    _client = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 30_000,
          retry: 1,
        },
      },
    });
  }
  return _client;
}
