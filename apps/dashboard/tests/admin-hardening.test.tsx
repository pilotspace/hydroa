/**
 * tests/admin-hardening.test.tsx — v50 harden-admin
 *
 * The authed admin chrome (AppShell) should give every route a progressive,
 * reduced-motion-safe entrance — wrapping its <main> content in a Reveal keyed
 * by the active route — WITHOUT changing the single <main id="main"> landmark,
 * and the /app route group keeps its failure + loading boundaries.
 *
 * RED before build: AppShell renders {children} directly in <main> with no
 * Reveal wrapper, so the `[data-slot="reveal"]` marker is absent.
 */

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";

import { AppShell } from "@/components/ui/app-shell";

function renderShell(activePath: string) {
  return render(
    <AppShell activePath={activePath} role={null} userEmail={null}>
      <div>ADMIN ROUTE CONTENT</div>
    </AppShell>,
  );
}

describe("admin shell entrance motion", () => {
  it("test_admin_content_renders_in_keyed_reveal", () => {
    renderShell("/app/keys");
    const main = screen.getByRole("main");
    // content present (Reveal is a passthrough — never hides admin content)
    expect(within(main).getByText("ADMIN ROUTE CONTENT")).toBeInTheDocument();
    // wrapped in the Reveal entrance marker
    const reveal = main.querySelector('[data-slot="reveal"]');
    expect(reveal).not.toBeNull();
    expect(reveal).toHaveTextContent("ADMIN ROUTE CONTENT");
  });

  it("test_main_landmark_single", () => {
    renderShell("/app");
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(document.getElementById("main")).not.toBeNull();
  });

  it("test_entrance_present_across_routes", () => {
    // a different active route still renders content inside the reveal wrapper
    renderShell("/app/usage");
    const main = screen.getByRole("main");
    expect(main.querySelector('[data-slot="reveal"]')).not.toBeNull();
  });
});

describe("admin route group failure + loading boundaries", () => {
  it("test_admin_failure_loading_boundaries_exist", () => {
    const base = resolve(process.cwd(), "app/(app)/app");
    expect(existsSync(resolve(base, "error.tsx")), "(app)/app/error.tsx").toBe(true);
    expect(existsSync(resolve(base, "loading.tsx")), "(app)/app/loading.tsx").toBe(true);
  });
});
