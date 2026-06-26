/**
 * tests/marketing-seo.test.tsx — v50 harden-marketing
 *
 * EC6: every marketing page emits a unique title + description + OpenGraph,
 * inheriting site-wide defaults from the root layout. Verifies the shared
 * buildMetadata helper, the root metadata base, and per-page uniqueness.
 *
 * RED before build: lib/seo.ts does not exist; pages export title-only metadata
 * (no description/openGraph); root layout has no metadata.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// app/layout imports next/font/google (Inter), which is unavailable in jsdom.
vi.mock("next/font/google", () => ({
  Inter: () => ({ className: "font-inter", variable: "--font-inter" }),
}));

import { buildMetadata } from "@/lib/seo";
import { metadata as rootMetadata } from "@/app/layout";

import LandingPage, { metadata as landingMeta } from "@/app/(marketing)/page";
import { metadata as pricingMeta } from "@/app/(marketing)/pricing/page";
import { metadata as docsMeta } from "@/app/(marketing)/docs/page";
import { metadata as blogMeta } from "@/app/(marketing)/blog/page";
import { metadata as statusMeta } from "@/app/(marketing)/status/page";
import { metadata as privacyMeta } from "@/app/(marketing)/legal/privacy/page";
import { metadata as securityMeta } from "@/app/(marketing)/legal/security/page";
import { metadata as termsMeta } from "@/app/(marketing)/legal/terms/page";

const PAGES = [
  ["landing", landingMeta],
  ["pricing", pricingMeta],
  ["docs", docsMeta],
  ["blog", blogMeta],
  ["status", statusMeta],
  ["privacy", privacyMeta],
  ["security", securityMeta],
  ["terms", termsMeta],
] as const;

function titleString(t: unknown): string {
  if (typeof t === "string") return t;
  if (t && typeof t === "object") {
    const o = t as { absolute?: string; default?: string };
    return o.absolute ?? o.default ?? "";
  }
  return "";
}

describe("buildMetadata", () => {
  it("test_build_metadata_shape", () => {
    const m = buildMetadata({ title: "X", description: "d", path: "/x" });
    expect(m.title).toBe("X");
    expect(m.description).toBe("d");
    expect(m.alternates?.canonical).toBe("/x");
    expect((m.openGraph as { title?: string })?.title).toBe("X");
    expect((m.twitter as { card?: string })?.card).toBeTruthy();
  });

  it("test_build_metadata_absolute_title", () => {
    const m = buildMetadata({ title: "X", description: "d", path: "/", absoluteTitle: true });
    expect(m.title).toEqual({ absolute: "X" });
  });
});

describe("root layout metadata base", () => {
  it("test_root_layout_metadata", () => {
    expect(rootMetadata.metadataBase).toBeInstanceOf(URL);
    expect((rootMetadata.title as { template?: string }).template).toBe("%s · Hydroa");
    expect(rootMetadata.description).toBeTruthy();
    expect(rootMetadata.openGraph).toBeTruthy();
  });
});

describe("marketing pages SEO coverage", () => {
  it("test_every_marketing_page_has_unique_seo", () => {
    const titles: string[] = [];
    for (const [name, meta] of PAGES) {
      const title = titleString(meta.title);
      expect(title, `${name} title`).toBeTruthy();
      expect(meta.description, `${name} description`).toBeTruthy();
      const og = meta.openGraph as { title?: unknown; description?: unknown } | undefined;
      expect(og?.title, `${name} og.title`).toBeTruthy();
      expect(og?.description, `${name} og.description`).toBeTruthy();
      titles.push(title);
    }
    expect(new Set(titles).size, "titles must be pairwise unique").toBe(titles.length);
  });
});

describe("landing renders (Reveal passthrough)", () => {
  it("test_landing_renders_with_reveal", () => {
    render(<LandingPage />);
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });
});
