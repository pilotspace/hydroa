/**
 * Re-exports the vi.fn() spies from the next/navigation mock so tests can
 * inspect calls without importing from next/navigation directly.
 *
 * Usage in a test:
 *   import { getRouterMock } from "./mocks/next-navigation";
 *   const router = getRouterMock();
 *   expect(router.push).toHaveBeenCalledWith("/keys");
 *
 * Note: vi.mock("next/navigation") is declared in setup.ts and hoisted.
 * We access the mocked module via vi.mocked() on the ESM import.
 */
import { vi } from "vitest";

// next/navigation is mocked globally in setup.ts.
// vi.mocked wraps the imported module so TypeScript knows all exports are spies.
// We use a lazy require so this file can be imported before the mock is wired.

type RouterMock = { push: ReturnType<typeof vi.fn>; replace: ReturnType<typeof vi.fn>; back: ReturnType<typeof vi.fn> };
type NavMock = {
  useRouter: ReturnType<typeof vi.fn>;
  redirect: ReturnType<typeof vi.fn>;
  push: ReturnType<typeof vi.fn>;
  replace: ReturnType<typeof vi.fn>;
};

function getNavMock(): NavMock {
  // In vitest with vi.mock() declared in setup, the mock is available as a
  // synchronous ESM mock and accessible via the module cache.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("next/navigation") as NavMock;
}

export function getRouterMock(): RouterMock {
  return getNavMock().useRouter() as RouterMock;
}

export function getRedirectMock(): ReturnType<typeof vi.fn> {
  return getNavMock().redirect;
}
