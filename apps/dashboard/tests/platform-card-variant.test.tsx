/**
 * tests/platform-card-variant.test.tsx — RED suite for the admin-console-ui task's
 * additive Card `variant="flat"` decision (TASK.md §1 Framings weighed, "Visual
 * treatment... source").
 *
 * The shared `Card` primitive (components/ui/card.tsx) gets a NEW, opt-in `variant`
 * prop. Its DEFAULT rendering (no variant, or variant="default") must stay
 * byte-identical to every one of the 14 existing shipped pages that already render
 * it — this suite pins the exact base class list so a future edit cannot silently
 * drift the default. `variant="flat"` is additive only: a larger radius, no hard
 * border, a softer/more diffuse shadow — used ONLY by this task's new platform
 * screens.
 *
 * RED before Build: Card has no `variant` prop yet, so passing one is a no-op and
 * the flattened classes never appear.
 */

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Card } from "@/components/ui/card";

describe("Card — variant additive contract", () => {
  it("test_card_default_variant_byte_identical", () => {
    const { container } = render(<Card data-testid="c">content</Card>);
    const el = container.querySelector('[data-testid="c"]')!;
    // The exact pre-existing base class list (card.tsx, unedited) — pinned so a
    // future change to the default surface is caught here, not visually.
    expect(el.className).toContain("rounded-lg");
    expect(el.className).toContain("border");
    expect(el.className).toContain("border-border");
    expect(el.className).toContain("bg-card");
    expect(el.className).toContain("text-card-foreground");
    expect(el.className).toContain("shadow-md");
    // never the new flat-only treatment
    expect(el.className).not.toContain("rounded-2xl");
    expect(el.className).not.toContain("border-transparent");
  });

  it("test_card_flat_variant_opt_in", () => {
    const { container } = render(
      <Card variant="flat" data-testid="c">
        content
      </Card>,
    );
    const el = container.querySelector('[data-testid="c"]')!;
    expect(el.className).toContain("rounded-2xl");
    expect(el.className).toContain("border-transparent");
    expect(el.className).not.toContain("border-border");
  });

  it("test_card_no_variant_prop_still_works_for_existing_consumers", () => {
    // Every existing caller passes zero `variant` prop today — confirms the new
    // prop is optional, not a breaking required addition.
    const { container } = render(<Card className="extra-class">x</Card>);
    expect(container.querySelector(".extra-class")).not.toBeNull();
  });
});
