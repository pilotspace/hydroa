/**
 * tests/email-domain-routing.test.ts — RED suite for `domain-aware-auth-routing`
 * TASK.md §3 (FROZEN @ v1), the PURE-MODULE half: normalizeEmailDomain /
 * isWellShapedDomain / classifyEmailDomain / PUBLIC_EMAIL_DOMAINS, plus the two
 * STRUCTURAL proofs the contract names:
 *
 *   (P3) static reachability — email-domain-routing.ts's import graph is IO-free
 *   (P4) list parity        — the TS provider list is set-equal to the gateway's
 *                             PUBLIC_EMAIL_DOMAINS frozenset, read from the .py
 *                             source at test time
 *
 * Persona: Application Security Engineer (`.add/personas/appsec-engineer.md`).
 * Its rule "an unknown id and a cross-tenant id return the IDENTICAL response …
 * this is what closes the enumeration oracle, not an after-the-fact check" is
 * M11's direct ancestor: the identical-render property must hold BY
 * CONSTRUCTION. P3 is what makes it structural rather than merely asserted — a
 * module with no imports and no IO identifiers CANNOT reach the network on ANY
 * input, so there is nothing for a later reviewer to take on faith.
 *
 * A failure of P3 or P4 is a SECURITY HARD-STOP (§3), never a flaky retry.
 *
 * RED reason (today, before Build): apps/dashboard/lib/email-domain-routing.ts
 * does not exist — every import below fails to resolve, and the P3 source read
 * throws ENOENT. Both fail for the RIGHT reason: missing implementation.
 *
 * NOTE on environment: §3 asks for "a node-environment test" for P4. This file
 * runs in the suite's default jsdom environment (the `legacy` vitest project's
 * setupFiles assume jsdom globals) and uses `node:fs` directly for the file
 * read. The substance §3 specifies — reading the real .py source off disk at
 * test time rather than trusting a hand-copied duplicate — is unchanged; only
 * the environment label differs. Recorded here rather than silently decided.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  PUBLIC_EMAIL_DOMAINS,
  classifyEmailDomain,
  isWellShapedDomain,
  normalizeEmailDomain,
  type EmailDomainClass,
} from "@/lib/email-domain-routing";

const REPO_ROOT = path.resolve(__dirname, "../../..");
const CLASSIFIER_SRC = path.join(REPO_ROOT, "apps/dashboard/lib/email-domain-routing.ts");
const GATEWAY_PROVIDER_LIST = path.join(
  REPO_ROOT,
  "apps/gateway/src/gateway/domain_capture/domain/public_email_domains.py",
);

describe("normalizeEmailDomain — M2: the ONE normalizer", () => {
  it("test_normalize_trims_lowercases_and_takes_text_after_last_at", () => {
    // covers: M2 — trim -> lowercase -> text after the LAST "@"
    expect(normalizeEmailDomain("Bob@GMAIL.com")).toBe("gmail.com");
    expect(normalizeEmailDomain("  bob@GmAiL.CoM  ")).toBe("gmail.com");
  });

  it("test_normalize_multiple_at_resolves_on_the_last_one", () => {
    // covers: M2 edge case — "weird@name@acme-corp.com" -> "acme-corp.com"
    expect(normalizeEmailDomain("weird@name@acme-corp.com")).toBe("acme-corp.com");
  });

  it("test_normalize_bare_domain_without_at_is_taken_as_is", () => {
    // covers: M2 — a value with no "@" is taken as-is (matches resolveSsoDomain)
    expect(normalizeEmailDomain("acme-corp.com")).toBe("acme-corp.com");
    expect(normalizeEmailDomain("  ACME-CORP.COM ")).toBe("acme-corp.com");
  });

  it("test_normalize_returns_empty_when_nothing_usable", () => {
    // covers: M2 / R3 — "" when nothing usable
    expect(normalizeEmailDomain("")).toBe("");
    expect(normalizeEmailDomain("   ")).toBe("");
    expect(normalizeEmailDomain("bob@")).toBe("");
  });
});

describe("isWellShapedDomain — M4: the TS mirror of normalize_domain's ACCEPT rules", () => {
  it("test_well_shaped_accepts_multi_label_hostnames", () => {
    // covers: M4 — >= 2 labels of [a-z0-9-], no leading/trailing hyphen
    for (const d of ["acme-corp.com", "a.co", "sub.acme-corp.co.uk", "x1.example"]) {
      expect(isWellShapedDomain(d), d).toBe(true);
    }
  });

  it("test_well_shaped_rejects_single_label_ip_literal_and_bad_labels", () => {
    // covers: M4, R4 — the cases normalize_domain raises DomainInvalidError on.
    // Deliberately STRICTER than the shipped LoginForm.validateSsoDomain regex (R-g).
    const rejected = [
      "localhost", // single label
      "192.168.1.1", // bare IPv4 literal
      "-acme.com", // leading hyphen
      "acme-.com", // trailing hyphen
      "acme .com", // whitespace
      "acme_corp.com", // underscore is not [a-z0-9-]
      "", // empty
      `${"a".repeat(64)}.com`, // label > 63 chars
      `${`${"a".repeat(60)}.`.repeat(5)}com`, // total > 253 chars
    ];
    for (const d of rejected) {
      expect(isWellShapedDomain(d), `expected ${JSON.stringify(d)} to be rejected`).toBe(false);
    }
  });
});

describe("classifyEmailDomain — M1/M3/M4/M5: exactly one of public|corporate|unknown", () => {
  it("test_public_provider_classifies_public_case_insensitively", () => {
    // covers: M2, M3 — "Bob@GMAIL.com" and "bob@gmail.com" classify identically
    expect(classifyEmailDomain("Bob@GMAIL.com")).toBe("public");
    expect(classifyEmailDomain("bob@gmail.com")).toBe("public");
    expect(classifyEmailDomain("  bob@GmAiL.CoM  ")).toBe("public");
    expect(classifyEmailDomain("Bob@GMAIL.com")).toBe(classifyEmailDomain("bob@gmail.com"));
  });

  it("test_every_listed_provider_classifies_public", () => {
    // covers: M3 — the whole list is live, not just the one entry a demo uses
    for (const domain of PUBLIC_EMAIL_DOMAINS) {
      expect(classifyEmailDomain(`someone@${domain}`), domain).toBe("public");
    }
  });

  it("test_corporate_domain_classifies_corporate", () => {
    // covers: M4 — well-shaped and not on the public list
    expect(classifyEmailDomain("dana@acme-corp.com")).toBe("corporate");
    expect(classifyEmailDomain("dana@nobody-here.example")).toBe("corporate");
  });

  it("test_subdomain_of_public_provider_is_not_public", () => {
    // covers: M3/M4 edge case — exact match only; a subdomain never matches its
    // parent, mirroring normalize_domain's no-subdomain-matches-parent rule.
    expect(classifyEmailDomain("bob@mail.gmail.com")).toBe("corporate");
  });

  it("test_multiple_at_classifies_on_the_last_domain", () => {
    // covers: M2 edge case
    expect(classifyEmailDomain("weird@name@acme-corp.com")).toBe("corporate");
  });

  it("test_empty_or_no_domain_part_classifies_unknown", () => {
    // covers: M5, R3 (ROUTE_NEUTRAL_NO_DOMAIN) — the SAFE DEFAULT
    for (const raw of ["", "   ", "bob", "bob@", "@"]) {
      expect(classifyEmailDomain(raw), JSON.stringify(raw)).toBe("unknown");
    }
  });

  it("test_malformed_domain_classifies_unknown", () => {
    // covers: M5, R4 (ROUTE_NEUTRAL_MALFORMED_DOMAIN) — fail SAFE, never guess.
    // The exact §2 scenario list.
    const malformed = [
      "bob@localhost",
      "bob@192.168.1.1",
      "bob@ acme.com",
      `bob@${"a".repeat(300)}`,
      "bob@-acme.com",
    ];
    for (const raw of malformed) {
      expect(classifyEmailDomain(raw), JSON.stringify(raw)).toBe("unknown");
    }
  });

  it("test_classification_is_total_and_deterministic", () => {
    // covers: M1 — exactly one of three values, and the SAME input always yields
    // the SAME output (a pure function has no hidden second input).
    const valid: EmailDomainClass[] = ["public", "corporate", "unknown"];
    const samples = ["bob@gmail.com", "dana@acme-corp.com", "bob@localhost", "", "weird@@"];
    for (const raw of samples) {
      const first = classifyEmailDomain(raw);
      expect(valid).toContain(first);
      // Called 50x — a memoized network lookup or a lazily-initialised cache
      // would show up as a differing answer on some later call.
      for (let i = 0; i < 50; i++) expect(classifyEmailDomain(raw)).toBe(first);
    }
  });
});

/**
 * (P3) STATIC REACHABILITY — the load-bearing structural proof.
 *
 * M11 says the render is a pure function of (typed string, static list). An
 * assertion that "no fetch happened during this test" only proves it for the
 * inputs the test tried. A module that imports NOTHING and names no IO
 * identifier cannot reach the network on ANY input — a proof over all inputs,
 * and what makes R1/R2 (ROUTE_EXISTENCE_LOOKUP_FORBIDDEN /
 * ROUTE_NETWORK_PROBE_FORBIDDEN) unbuildable rather than merely unbuilt.
 */
describe("P3 — email-domain-routing.ts has an IO-free import graph (R1, R2)", () => {
  function classifierSource(): string {
    return readFileSync(CLASSIFIER_SRC, "utf8");
  }

  /** Source with comments stripped, so a word inside a doc comment is not a hit. */
  function codeOnly(src: string): string {
    return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  }

  it("test_p3_module_has_zero_imports", () => {
    // covers: R1, R2 — a leaf module with no imports and no barrel (§5 batch 2:
    // "a barrel import is how IO sneaks into an 'IO-free' module").
    const code = codeOnly(classifierSource());

    expect(code).not.toMatch(/^\s*import\s/m);
    expect(code).not.toMatch(/\brequire\s*\(/);
    expect(code).not.toMatch(/\bimport\s*\(/); // dynamic import
    expect(code).not.toMatch(/\bexport\s+[^;]*\bfrom\s+["']/); // re-export barrel
  });

  it("test_p3_module_names_no_io_or_existence_lookup_identifier", () => {
    // covers: R1, R2 — the FORBIDDEN list from §3, verbatim, plus the two
    // gateway existence predicates §0 named specifically so §3 could forbid
    // them by name.
    const code = codeOnly(classifierSource());

    const forbidden = [
      "fetch",
      "XMLHttpRequest",
      "sendBeacon",
      "navigator",
      "localStorage",
      "sessionStorage",
      "document",
      "cookie",
      "window",
      "next/navigation",
      "process.env",
      "resolve_verified_tenant",
      "resolve_verified_tenant_for_raw_domain",
      "handleSso",
    ];
    for (const token of forbidden) {
      expect(code, `email-domain-routing.ts must not reference ${token}`).not.toContain(token);
    }
  });

  it("test_p3_guard_itself_is_wired_to_the_real_file", () => {
    // A guard that reads the wrong path passes vacuously forever. Prove the
    // source it inspects is the module the tests above actually imported.
    const src = classifierSource();
    expect(src).toContain("classifyEmailDomain");
    expect(src).toContain("PUBLIC_EMAIL_DOMAINS");
    expect(src.length).toBeGreaterThan(200);
  });
});

/**
 * (P4) LIST PARITY — R6 / ROUTE_PROVIDER_LIST_DRIFT.
 *
 * R-c: a second copy of a security-relevant list is the exact two-copies
 * failure mode. This guard reads the REAL .py source at test time, so adding an
 * entry to either side alone fails the build.
 */
describe("P4 — TS and gateway provider lists cannot silently drift (R6)", () => {
  /** Extract the string literals inside the frozenset({...}) in the .py source. */
  function gatewayProviderDomains(): Set<string> {
    const py = readFileSync(GATEWAY_PROVIDER_LIST, "utf8");
    const block = py.match(
      /PUBLIC_EMAIL_DOMAINS:\s*frozenset\[str\]\s*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/,
    );
    if (!block) {
      throw new Error(
        "could not locate the PUBLIC_EMAIL_DOMAINS frozenset literal in public_email_domains.py — " +
          "the parity guard is not reading what it thinks it is",
      );
    }
    const entries = block[1].match(/"([^"]+)"/g) ?? [];
    return new Set(entries.map((e) => e.slice(1, -1)));
  }

  it("test_p4_lists_are_set_equal", () => {
    // covers: M3, R6
    const gateway = gatewayProviderDomains();
    const client = new Set(PUBLIC_EMAIL_DOMAINS);

    const missingFromClient = [...gateway].filter((d) => !client.has(d)).sort();
    const extraInClient = [...client].filter((d) => !gateway.has(d)).sort();

    expect({ missingFromClient, extraInClient }).toEqual({
      missingFromClient: [],
      extraInClient: [],
    });
  });

  it("test_p4_guard_fails_in_the_failing_direction", () => {
    // §6: "a guard never exercised in the failing direction is not evidence."
    // Simulate a one-sided edit and prove the comparison the guard performs
    // actually rejects it.
    const gateway = gatewayProviderDomains();
    const drifted = new Set(gateway);
    drifted.add("bogus-drift-provider.example");

    const missingFromClient = [...gateway].filter((d) => !drifted.has(d));
    const extraInClient = [...drifted].filter((d) => !gateway.has(d));
    expect(missingFromClient).toEqual([]);
    expect(extraInClient).toEqual(["bogus-drift-provider.example"]);
  });

  it("test_p4_list_is_non_trivial_and_lowercase_apex_only", () => {
    // A parity guard over two EMPTY sets passes vacuously. Pin the real shape.
    expect(PUBLIC_EMAIL_DOMAINS.size).toBeGreaterThanOrEqual(22);
    expect(PUBLIC_EMAIL_DOMAINS.has("gmail.com")).toBe(true);
    for (const d of PUBLIC_EMAIL_DOMAINS) {
      expect(d).toBe(d.toLowerCase());
      expect(d).toMatch(/^[a-z0-9.-]+\.[a-z]{2,}$/);
    }
  });
});
