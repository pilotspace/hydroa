/**
 * tests/signup-email-seed.test.tsx — RED suite for `unified-signin-entry`
 * TASK.md §3 (FROZEN @ v1) M13: SignupForm gains a ONE-SHOT `?email=` seed,
 * mirroring its shipped `?account_type=business` seed exactly (see
 * tests/homepage-cta-intent-split.test.tsx's own precedent for the
 * useSearchParams mocking convention this suite reuses).
 *
 * RED reason (today, before Build): SignupForm's `#signup_email` field is
 * seeded from nothing but its own onChange — a `?email=` search param has
 * zero effect on it today, so `toHaveValue("dana@acme-corp.com")` fails (the
 * field stays empty).
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSearchParams } from "next/navigation";

import { SignupForm } from "@/components/auth/SignupForm";

function setEmailParam(query: string | null) {
  const params = new URLSearchParams(query ?? "");
  vi.mocked(useSearchParams).mockReturnValue(params as ReturnType<typeof useSearchParams>);
}

beforeEach(() => {
  setEmailParam(null);
});

describe("SignupForm — one-shot ?email= seed (M13)", () => {
  it("test_email_query_param_prefills_the_signup_email_field", async () => {
    // covers: M13
    setEmailParam("email=dana%40acme-corp.com");
    render(<SignupForm />);

    await expect
      .poll(() => screen.getByLabelText(/^email$/i))
      .toHaveValue("dana@acme-corp.com");
  });

  it("test_seed_is_one_shot_and_a_later_manual_edit_wins", async () => {
    // covers: M13 — a later manual edit is never re-overwritten by the effect
    // (which has [] deps and never re-fires after mount).
    const user = userEvent.setup();
    setEmailParam("email=dana%40acme-corp.com");
    render(<SignupForm />);

    const emailField = await screen.findByLabelText(/^email$/i);
    await expect.poll(() => emailField).toHaveValue("dana@acme-corp.com");

    await user.clear(emailField);
    await user.type(emailField, "someone-else@example.com");
    expect(emailField).toHaveValue("someone-else@example.com");

    // Prove it never reverts on a later render/rerender.
    await screen.findByLabelText(/tenant name/i);
    expect(emailField).toHaveValue("someone-else@example.com");
  });

  it("test_absent_email_param_is_a_no_op_byte_identical_to_today", async () => {
    // covers: M13 — plain /signup leaves the field empty, byte-identical.
    setEmailParam(null);
    render(<SignupForm />);

    expect(screen.getByLabelText(/^email$/i)).toHaveValue("");
  });
});
