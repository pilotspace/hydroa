/**
 * tests/failure-state-segments.test.tsx — v50 failure-state-segments
 *
 * App-Router failure segments + the shared leak-guarded RouteError. The security
 * crux (M2): an error boundary must NEVER render error.message/stack — only
 * generic copy + the safe digest.
 *
 * RED before build: the segment modules + RouteError do not exist → MODULE_NOT_FOUND.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RouteError } from "@/components/ui/route-error";
import GlobalError from "@/app/global-error";
import NotFound from "@/app/not-found";
import AppLoading from "@/app/(app)/app/loading";
import AppError from "@/app/(app)/app/error";
import MarketingError from "@/app/(marketing)/error";
import AuthError from "@/app/(auth)/error";

const SECRET = "SECRET_INTERNAL_DETAIL";

function err(message = "boom", digest?: string): Error & { digest?: string } {
  const e = new Error(message) as Error & { digest?: string };
  if (digest) e.digest = digest;
  return e;
}

describe("RouteError", () => {
  it("test_route_error_renders_and_retries", async () => {
    const reset = vi.fn();
    render(<RouteError error={err()} reset={reset} surface="dashboard" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /retry|try again/i });
    await userEvent.click(retry);
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("test_route_error_hides_message", () => {
    const { container } = render(
      <RouteError error={err(SECRET, "dig-123")} reset={vi.fn()} />,
    );
    expect(container.textContent).not.toContain(SECRET);
    expect(container.textContent).toContain("dig-123");
  });
});

describe("GlobalError", () => {
  it("test_global_error_hides_message", () => {
    const { container } = render(
      <GlobalError error={err(SECRET)} reset={vi.fn()} />,
    );
    expect(container.textContent).not.toContain(SECRET);
    // a reset affordance is present
    expect(
      screen.getByRole("button", { name: /retry|try again/i }),
    ).toBeInTheDocument();
  });
});

describe("NotFound", () => {
  it("test_not_found_links_home", () => {
    const { container } = render(<NotFound />);
    expect(container.textContent).toContain("404");
    const home = container.querySelector('a[href="/"]');
    expect(home).not.toBeNull();
  });
});

describe("dashboard Loading", () => {
  it("test_loading_is_accessible", () => {
    render(<AppLoading />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
  });
});

describe("group error.tsx wrappers delegate to RouteError", () => {
  it("test_group_error_modules_delegate", () => {
    for (const ErrorComp of [AppError, MarketingError, AuthError]) {
      const { unmount } = render(<ErrorComp error={err()} reset={vi.fn()} />);
      expect(screen.getByRole("alert")).toBeInTheDocument();
      unmount();
    }
  });
});
