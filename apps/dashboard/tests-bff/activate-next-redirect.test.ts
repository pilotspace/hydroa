/**
 * activate-next-redirect.test.ts — RED unit suite for the open-redirect guard and
 * the /activate → /login → /activate round-trip helpers (device-activate-page M1, R-D).
 *
 * sanitizeNext is the SECURITY primitive: only a same-origin RELATIVE path survives;
 * every absolute URL, scheme-relative `//host`, scheme-smuggled, or backslash-smuggled
 * value collapses to null so login can never be turned into an open redirect.
 *
 * TRUE-RED REASON: the redirect-safety helpers in @/lib/bff-client + normalizeUserCode
 * in @/components/agent-activation/client do not exist yet.
 */

import { describe, it, expect } from "vitest";
import { sanitizeNext, loginNextTarget, buildLoginBounceUrl } from "@/lib/bff-client";
import { normalizeUserCode } from "@/components/agent-activation/client";

describe("sanitizeNext (open-redirect guard)", () => {
  it("keeps a same-origin relative path with the user_code query", () => {
    expect(sanitizeNext("/activate?user_code=BCDF-GHJK")).toBe("/activate?user_code=BCDF-GHJK");
  });

  it("keeps a bare relative path", () => {
    expect(sanitizeNext("/app/keys")).toBe("/app/keys");
  });

  it.each([
    ["absolute https", "https://evil.example/steal"],
    ["absolute http", "http://evil.example/steal"],
    ["scheme-relative //host", "//evil.example/steal"],
    ["backslash-smuggled", "/\\evil.example"],
    ["double backslash", "\\\\evil.example"],
    ["scheme with no slashes", "javascript:alert(1)"],
    ["data uri", "data:text/html,evil"],
    ["whitespace-smuggled scheme", " https://evil.example"],
    ["empty string", ""],
    ["not starting with slash", "app/keys"],
    ["null", null],
    ["undefined", undefined],
  ])("rejects %s → null", (_label, value) => {
    expect(sanitizeNext(value as string | null | undefined)).toBeNull();
  });
});

describe("loginNextTarget", () => {
  it("returns the sanitized next when same-origin relative", () => {
    expect(loginNextTarget("/activate?user_code=BCDF-GHJK")).toBe("/activate?user_code=BCDF-GHJK");
  });

  it("falls back to /app/keys for an off-origin next", () => {
    expect(loginNextTarget("https://evil.example")).toBe("/app/keys");
  });

  it("falls back to /app/keys when next is absent", () => {
    expect(loginNextTarget(null)).toBe("/app/keys");
  });
});

describe("normalizeUserCode (loose human input)", () => {
  it("normalizes lowercase/spaced/no-dash to XXXX-XXXX", () => {
    expect(normalizeUserCode(" bcdf ghjk ")).toBe("BCDF-GHJK");
  });

  it("keeps an already-canonical code", () => {
    expect(normalizeUserCode("BCDF-GHJK")).toBe("BCDF-GHJK");
  });

  it("re-inserts the dash for 8 bare chars", () => {
    expect(normalizeUserCode("bcdfghjk")).toBe("BCDF-GHJK");
  });

  it("leaves a non-8-char value uppercased and dash-free (hashes to no match)", () => {
    expect(normalizeUserCode("abc")).toBe("ABC");
  });
});

describe("buildLoginBounceUrl (return-path preservation on the /login bounce)", () => {
  it("encodes the current path+search into ?next=", () => {
    expect(buildLoginBounceUrl("/activate?user_code=BCDF-GHJK")).toBe(
      "/login?next=" + encodeURIComponent("/activate?user_code=BCDF-GHJK"),
    );
  });

  it("does not preserve an already-/login location (avoids a loop)", () => {
    expect(buildLoginBounceUrl("/login")).toBe("/login");
  });

  it.each([
    ["root", "/"],
    ["empty", ""],
    ["scheme-relative", "//evil.example"],
  ])("returns a bare /login for a non-meaningful %s return path", (_label, value) => {
    expect(buildLoginBounceUrl(value)).toBe("/login");
  });
});
