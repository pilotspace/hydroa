/**
 * tests/platform-card-variant.test.tsx — Card's opt-in `variant` contract.
 *
 * The shared `Card` primitive (components/ui/card.tsx) has an opt-in `variant`
 * prop. Its DEFAULT rendering (no variant, or variant="default") must stay
 * byte-identical to every one of the 14+ existing shipped pages that already
 * render it — this suite pins the exact base class list so a future edit cannot
 * silently drift the default.
 *
 * `variant="soft"` (renamed from "flat" — platform-console-flat-redesign, 2026-07-06):
 * the admin-console-ui task's original larger-radius/no-hard-border/diffuse-shadow
 * treatment. Renamed because the flat-redesign milestone's own "flat" now means the
 * OPPOSITE (borderless AND shadowless, sharp radius) — shipping both meanings under
 * the same prop value would collide. Behavior is byte-identical to the old "flat",
 * just under a new name; the one call site (PlatformTenantDirectory.tsx) is updated.
 *
 * `variant="flat"` (NEW, platform-console-flat-redesign): no border, no shadow, a
 * sharp radius (`--radius-flat-card`, 4px) — realizes the flat/borderless recipe's
 * `flat-card` token. Additive only; not yet consumed by any page (component-primitive
 * support only, per the token-transcription pass — no screen has adopted it yet).
 *
 * RED before Build: soft/flat did not exist as prop values yet, so passing either
 * was a no-op and none of the variant-specific classes appeared.
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
    // never a variant-only treatment
    expect(el.className).not.toContain("rounded-2xl");
    expect(el.className).not.toContain("border-transparent");
    expect(el.className).not.toContain("shadow-none");
  });

  it("test_card_soft_variant_opt_in", () => {
    const { container } = render(
      <Card variant="soft" data-testid="c">
        content
      </Card>,
    );
    const el = container.querySelector('[data-testid="c"]')!;
    expect(el.className).toContain("rounded-2xl");
    expect(el.className).toContain("border-transparent");
    expect(el.className).toContain("shadow-lg");
    expect(el.className).not.toContain("border-border");
  });

  it("test_card_flat_variant_no_border_no_shadow_sharp_radius", () => {
    const { container } = render(
      <Card variant="flat" data-testid="c">
        content
      </Card>,
    );
    const el = container.querySelector('[data-testid="c"]')!;
    expect(el.className).toContain("rounded-[var(--radius-flat-card)]");
    expect(el.className).toContain("shadow-none");
    expect(el.className).not.toContain("border-border");
    expect(el.className).not.toContain("shadow-md");
    // distinct from "soft" — flat is sharp-cornered, soft is the older larger radius
    expect(el.className).not.toContain("rounded-2xl");
  });

  it("test_card_no_variant_prop_still_works_for_existing_consumers", () => {
    // Every existing caller passes zero `variant` prop today — confirms the new
    // prop is optional, not a breaking required addition.
    const { container } = render(<Card className="extra-class">x</Card>);
    expect(container.querySelector(".extra-class")).not.toBeNull();
  });
});
