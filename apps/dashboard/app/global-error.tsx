"use client";

/**
 * global-error.tsx — the root error boundary (v50). It replaces the ROOT layout
 * when the layout itself throws, so it must render its OWN <html>/<body> and
 * cannot depend on the font / QueryClient providers. Kept dependency-light and
 * inline-styled so it renders even if the stylesheet failed to load.
 *
 * SECURITY: never renders error.message/stack — generic copy + safe digest only.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, sans-serif",
          display: "flex",
          minHeight: "100vh",
          alignItems: "center",
          justifyContent: "center",
          margin: 0,
          background: "#0c1015",
          color: "#eaeff5",
        }}
      >
        <div role="alert" style={{ maxWidth: "28rem", padding: "2rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 600, margin: "0 0 0.5rem" }}>
            Something went wrong
          </h1>
          <p style={{ fontSize: "0.875rem", opacity: 0.8, margin: "0 0 1rem" }}>
            An unexpected error occurred. Please try again, or come back in a moment.
          </p>
          {error.digest ? (
            <p style={{ fontSize: "0.75rem", opacity: 0.6, margin: "0 0 1rem" }}>
              Reference: {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              cursor: "pointer",
              borderRadius: "0.5rem",
              border: "1px solid #2f6df0",
              background: "#2f6df0",
              color: "#ffffff",
              padding: "0.5rem 1rem",
              fontSize: "0.875rem",
              fontWeight: 500,
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
