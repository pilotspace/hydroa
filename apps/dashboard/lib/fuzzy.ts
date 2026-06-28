/**
 * lib/fuzzy.ts — the SINGLE seam that imports fuse.js.
 *
 * Tin overrode the no-new-dep default at the v54 model-catalog freeze: fuzzy search is
 * typo-tolerant via fuse.js. Isolating the import here keeps the dependency behind one
 * boundary (audited via `npm audit --omit=dev`) and keeps callers (DataTable) test-simple.
 */
import Fuse from "fuse.js";

/**
 * Rank `items` against `query` over the given string `keys`, best-match first.
 *
 * An empty / whitespace-only query returns the items unchanged (original order) — the
 * "no filter applied" contract. A non-matching query returns `[]`.
 */
export function fuzzySearch<T>(items: T[], query: string, keys: (keyof T)[]): T[] {
  const q = query.trim();
  if (q === "") return items;

  const fuse = new Fuse(items, {
    keys: keys as string[],
    threshold: 0.4, // moderate fuzziness: tolerates typos without matching everything
    ignoreLocation: true, // match anywhere in the field (ids are long slugs)
  });
  return fuse.search(q).map((result) => result.item);
}
