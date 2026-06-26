import Link from "next/link";

/**
 * not-found.tsx — the global 404 surface (v50). On-brand, with a clear path
 * back home so a mistyped/stale URL never dead-ends. Server Component.
 */
export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center gap-3 p-10 text-center">
      <p className="text-5xl font-semibold tracking-tight text-foreground">404</p>
      <h1 className="text-lg font-medium text-foreground">Page not found</h1>
      <p className="text-sm text-muted-foreground">
        The page you&apos;re looking for doesn&apos;t exist or has moved.
      </p>
      <Link
        href="/"
        className="mt-2 inline-flex items-center rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Back to home
      </Link>
    </div>
  );
}
