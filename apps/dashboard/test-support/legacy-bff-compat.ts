/**
 * test-support/legacy-bff-compat.ts
 *
 * v18 (auth-me-session-verify): the same-origin GET /api/auth/me default for the
 * legacy suite was MOVED into the legacy INITIAL handlers (tests/mocks/handlers.ts)
 * so that `afterEach(server.resetHandlers())` PRESERVES it across every test.
 *
 * Previously this file registered that default via a runtime `server.use(...)` at
 * setup-eval time — but resetHandlers() wipes runtime handlers after test #1, so
 * later useCurrentUser renders leaked an unhandled /api/auth/me (the carried v17
 * 0-leak, surfacing as "up to 7 under load"). An initial handler closes that leak
 * for good. Per-test role overrides (e.g. "owner") still work via server.use (LIFO).
 *
 * This module is intentionally a no-op now; it stays in the legacy project's
 * setupFiles so the wiring is explicit and easy to extend if a future same-origin
 * BFF default is needed.
 */

export {};
