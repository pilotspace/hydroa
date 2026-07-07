/**
 * error-heading — a11y audit fix (persona sweep, W2): the shared ErrorState rendered its
 * title as a bare <p>, so App-Router error boundaries (RouteError) had NO heading — a
 * screen-reader user landing on an errored page got an alert with no h1 to orient by.
 *
 * Fix: ErrorState gains an optional `titleAs` (default "p", so every in-page caller that
 * sits under an existing page h1 stays byte-compatible — no second h1); RouteError, which
 * REPLACES the whole page segment, opts into "h1".
 *
 * RED before build: ErrorState has no titleAs prop and always renders a <p>; RouteError
 * exposes no heading.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorState } from "@/components/ui/states";
import { RouteError } from "@/components/ui/route-error";

describe("ErrorState — optional heading element", () => {
  it("defaults to a non-heading paragraph (byte-compatible with in-page callers under a page h1)", () => {
    render(<ErrorState title="Something failed" />);
    expect(screen.queryByRole("heading")).toBeNull();
    expect(screen.getByText("Something failed").tagName.toLowerCase()).toBe("p");
  });

  it("renders the title as a real heading when titleAs is set", () => {
    render(<ErrorState title="Something failed" titleAs="h2" />);
    expect(screen.getByRole("heading", { level: 2, name: "Something failed" })).toBeInTheDocument();
  });

  it("keeps role=alert regardless of the title element", () => {
    render(<ErrorState title="Something failed" titleAs="h1" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("RouteError — error boundaries expose a real h1", () => {
  it("renders the error title as an h1", () => {
    render(<RouteError error={new Error("boom")} reset={vi.fn()} surface="dashboard" />);
    expect(screen.getByRole("heading", { level: 1, name: /something went wrong/i })).toBeInTheDocument();
  });
});
