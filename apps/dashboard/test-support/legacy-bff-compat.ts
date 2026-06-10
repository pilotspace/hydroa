/**
 * test-support/legacy-bff-compat.ts
 *
 * Registers default handlers for the BFF same-origin routes on the legacy
 * msw server (tests/mocks/server) so that components modified to call
 * /api/auth/me (UsagePage useCurrentUser hook) and /api/auth/login
 * (LoginForm/SignupForm BFF endpoints) don't trigger onUnhandledRequest errors
 * in the legacy test suite.
 *
 * These defaults return benign values:
 *   GET /api/auth/me  → role:"member" (no Edit Budget button by default;
 *                       individual tests that need "owner" override via server.use)
 *
 * This file is included only in the "legacy" vitest project setupFiles,
 * AFTER tests/setup.ts, so it runs after the legacy msw server is started.
 */

import { http, HttpResponse } from "msw";
import { server } from "../tests/mocks/server";

const APP = "http://localhost:3000";

server.use(
  http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "00000000-0000-0000-0000-000000000001",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      email: "ada@acme.io",
      role: "member",
      exp: Math.floor(Date.now() / 1000) + 86400,
    })
  )
);
