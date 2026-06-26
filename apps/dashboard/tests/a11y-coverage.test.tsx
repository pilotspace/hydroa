/**
 * tests/a11y-coverage.test.tsx — v50 a11y-ci-coverage
 *
 * Closes the axe gap on the key surfaces that were never accessibility-checked
 * (the auth forms + the new failure segments) and proves the reusable
 * serious/critical helper works. Runs in the standard (CI) vitest suite.
 *
 * RED before build: expectNoSeriousViolations is not exported from
 * @/test-support/axe yet → import resolves to undefined / throws.
 */

import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

import { expectNoSeriousViolations } from "@/test-support/axe";
import { LoginForm } from "@/components/auth/LoginForm";
import { SignupForm } from "@/components/auth/SignupForm";
import NotFound from "@/app/not-found";
import { RouteError } from "@/components/ui/route-error";

describe("expectNoSeriousViolations helper", () => {
  it("test_helper_throws_on_serious", async () => {
    // an <img> with no alt text is a serious/critical axe violation
    const { container } = render(<img src="/x.png" />);
    await expect(expectNoSeriousViolations(container)).rejects.toThrow(/a11y|violation/i);
  });

  it("test_helper_passes_when_clean", async () => {
    const { container } = render(
      <main>
        <h1>Clean</h1>
        <p>No serious issues here.</p>
      </main>,
    );
    await expect(expectNoSeriousViolations(container)).resolves.toBeUndefined();
  });
});

describe("key surfaces pass the serious/critical bar", () => {
  it("test_login_form_no_serious", async () => {
    const { container } = render(<LoginForm />);
    await expectNoSeriousViolations(container);
  });

  it("test_signup_form_no_serious", async () => {
    const { container } = render(<SignupForm />);
    await expectNoSeriousViolations(container);
  });

  it("test_not_found_no_serious", async () => {
    const { container } = render(<NotFound />);
    await expectNoSeriousViolations(container);
  });

  it("test_route_error_no_serious", async () => {
    const e = new Error("boom") as Error & { digest?: string };
    const { container } = render(<RouteError error={e} reset={vi.fn()} surface="dashboard" />);
    await expectNoSeriousViolations(container);
  });
});
