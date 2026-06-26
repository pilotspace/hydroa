import type { Metadata } from "next";

/**
 * lib/seo.ts — the single SEO shape every marketing page reuses (v50
 * harden-marketing). Produces a unique title + description + canonical +
 * OpenGraph + Twitter card, inheriting the site-wide defaults set on the root
 * layout (metadataBase, title template, default OG). Additive — no content change.
 */
export const SITE_NAME = "Hydroa";

export interface BuildMetadataOptions {
  /** Page title — a string slots into the root `%s · Hydroa` template. */
  title: string;
  /** Unique meta description (also used for OG/Twitter). */
  description: string;
  /** Route path for the canonical URL (resolved against metadataBase). */
  path: string;
  /** Opt out of the title template (e.g. the landing brand title). */
  absoluteTitle?: boolean;
}

export function buildMetadata({
  title,
  description,
  path,
  absoluteTitle = false,
}: BuildMetadataOptions): Metadata {
  return {
    title: absoluteTitle ? { absolute: title } : title,
    description,
    alternates: { canonical: path },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      title,
      description,
      url: path,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
    },
  };
}
