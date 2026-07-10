/**
 * tests/platform-nav.test.tsx — RED suite for the admin-console-ui task's
 * superadmin-only "Platform" nav entry (TASK.md M3).
 *
 * Unlike every existing nav item (a DENYLIST: hidden from specific named lower
 * roles, fail-open/visible by default — see tests-bff/nav-role-filter.test.tsx's
 * own test_unknown_role_fails_open), "Platform" -> "Tenants" is an ALLOWLIST:
 * visible if and only if role === "superadmin" exactly, hidden during the
 * identity-loading window (role=null/undefined) and for every other role,
 * including owner (§1 Framings weighed, ⚠ Assumption #3).
 *
 * Mirrors tests/admin-hardening.test.tsx's own direct-AppShell-render convention
 * (no MSW needed — role is a plain prop).
 *
 * RED before Build: AppShell has no "Platform" nav entry yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { AppShell } from "@/components/ui/app-shell";

const NON_SUPERADMIN_ROLES: Array<string | null | undefined> = [
  null,
  undefined,
  "member",
  "admin",
  "operator",
  "billing_admin",
  "viewer",
  "owner",
];

function primaryNav() {
  return screen.getByRole("navigation", { name: /primary/i });
}

function mobileNav() {
  return screen.getByRole("navigation", { name: /site/i });
}

describe("AppShell — Platform nav entry (allowlist, M3)", () => {
  it("test_nav_hidden_for_all_non_superadmin_roles_incl_loading", () => {
    for (const role of NON_SUPERADMIN_ROLES) {
      const { unmount } = render(
        <AppShell role={role}>
          <div>content</div>
        </AppShell>,
      );
      expect(
        within(primaryNav()).queryByRole("link", { name: /tenants/i }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/^platform$/i)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("test_nav_visible_for_superadmin_desktop_and_mobile", () => {
    render(
      <AppShell role="superadmin">
        <div>content</div>
      </AppShell>,
    );

    // Desktop rail (Primary landmark)
    const desktopLink = within(primaryNav()).getByRole("link", { name: /tenants/i });
    expect(desktopLink).toBeInTheDocument();
    expect(desktopLink).toHaveAttribute("href", "/app/platform/tenants");
    expect(within(primaryNav()).getByText(/^platform$/i)).toBeInTheDocument();

    // Mobile sheet — portal-mounted only once opened (AppShell's own documented
    // convention: keeps exactly ONE Primary landmark by default).
    fireEvent.click(screen.getByRole("button", { name: /open navigation/i }));
    const mobileLink = within(mobileNav()).getByRole("link", { name: /tenants/i });
    expect(mobileLink).toBeInTheDocument();
    expect(mobileLink).toHaveAttribute("href", "/app/platform/tenants");
  });

  it("test_nav_owner_role_still_hidden_despite_seeing_every_other_admin_link", () => {
    // Named divergence from the existing fail-open convention: an owner sees
    // every OTHER admin/owner-tier link, but never "Platform" (⚠ Assumption #3).
    render(
      <AppShell role="owner">
        <div>content</div>
      </AppShell>,
    );
    const n = primaryNav();
    expect(within(n).getByRole("link", { name: /settings/i })).toBeInTheDocument();
    expect(within(n).queryByRole("link", { name: /tenants/i })).not.toBeInTheDocument();
  });
});
