import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "Blog",
  description:
    "Product updates, engineering notes, and best practices for running multi-provider AI infrastructure with Hydroa.",
  path: "/blog",
});

/**
 * Public /blog index scaffold — structure ready for real posts.
 * Frozen contract (§3 v1): PUBLIC, no cookie/fetch. Honest empty-state until a
 * content pipeline lands (SPEC delta).
 */
export default function BlogPage() {
  return (
    <main id="main">
      <section aria-labelledby="blog-heading" className="px-4 py-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl text-center">
          <h1 id="blog-heading" className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            Blog
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            Product updates, engineering notes, and AI-operations guidance.
          </p>
        </div>
        <div className="mx-auto mt-14 max-w-2xl">
          <div
            role="status"
            className="rounded-lg border border-dashed border-border bg-muted/30 p-12 text-center"
          >
            <h2 className="text-lg font-semibold text-foreground">No posts yet</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              We&rsquo;re just getting started. Check back soon for the first post.
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
