/**
 * design-system-foundation — shared primitive + state + shell render contracts.
 * RED before build: components/ui/* and the AppShell do not exist yet.
 * Asserts OBSERVABLE behavior (roles/aria/markers), never internal class strings,
 * except the loading-state marker the existing suites fall back on (T10/T23).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loading, Empty, ErrorState, Success } from "@/components/ui/states";
import { AppShell } from "@/components/ui/app-shell";

describe("M3 · Button", () => {
  it("renders a native button, applies the cva variant + focus-visible ring, fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    const btn = screen.getByRole("button", { name: "Save" });
    expect(btn.tagName).toBe("BUTTON");
    // §3 C: default variant is token-backed + carries the focus-visible ring
    expect(btn).toHaveClass("bg-primary");
    expect(btn.className).toMatch(/focus-visible:ring-2/);
    await userEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("applies the destructive variant class", () => {
    render(<Button variant="destructive">Delete</Button>);
    const btn = screen.getByRole("button", { name: "Delete" });
    expect(btn).toHaveClass("bg-destructive");
  });
});

describe("M3 · Dialog (Radix — keyboard operable)", () => {
  it("opens, exposes role=dialog, and closes on Escape", async () => {
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent>
          <DialogTitle>Confirm</DialogTitle>
          <p>Body</p>
        </DialogContent>
      </Dialog>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    // This Radix version signals an accessible modal via a label + a real focus-trap
    // (focus-guards, not aria-modal). Assert the substantive guarantees:
    expect(dialog).toHaveAttribute("aria-labelledby");
    expect(within(dialog).getByText("Confirm")).toBeInTheDocument();
    expect(dialog.contains(document.activeElement)).toBe(true); // focus moved in (focus-trap)
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("M4 · state components preserve observable markers", () => {
  it("Loading exposes role=status + aria-busy and a spin/pulse marker", () => {
    const { container } = render(<Loading label="Loading usage" />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(
      status.matches('[aria-busy="true"]') &&
        (container.querySelector(".animate-spin, .animate-pulse") !== null),
    ).toBe(true);
  });

  it("ErrorState renders role=alert with the caller's title verbatim", () => {
    render(<ErrorState title="Internal Server Error" />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("Internal Server Error")).toBeInTheDocument();
  });

  it("Empty and Success render caller copy", () => {
    const { rerender } = render(<Empty title="No usage records yet" />);
    expect(screen.getByText("No usage records yet")).toBeInTheDocument();
    rerender(<Success title="Saved" />);
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  // accessibility-auditor sweep, 2026-07-06: text-destructive (red-600) on bg-destructive/5
  // measured 4.47:1 on a white card / 4.28:1 on the app canvas — both under AA. Same fix
  // shape as Badge's destructive variant: swap to the AA-safe text-destructive-text.
  it("ErrorState uses the AA-safe destructive text color, not the raw semantic one", () => {
    render(<ErrorState title="Internal Server Error" />);
    const classes = screen.getByRole("alert").className.split(" ");
    expect(classes).toContain("text-destructive-text");
    expect(classes).not.toContain("text-destructive");
  });

  // Same cross-check: Success used raw text-success (emerald-600, 3.55:1 on its own
  // bg-success/5 — under AA) instead of the already-shipped text-success-text alias.
  it("Success uses the AA-safe success text color, not the raw semantic one", () => {
    const { container } = render(<Success title="Saved" />);
    const classes = container.firstElementChild!.className.split(" ");
    expect(classes).toContain("text-success-text");
    expect(classes).not.toContain("text-success");
  });
});

describe("M5 · responsive a11y shell", () => {
  it("renders skip-link to #main, a Primary nav landmark, and main#main", () => {
    render(
      <AppShell>
        <p>Page body</p>
      </AppShell>,
    );
    const skip = screen.getByRole("link", { name: /skip to (main )?content/i });
    expect(skip).toHaveAttribute("href", "#main");

    const nav = screen.getByRole("navigation", { name: /primary/i });
    expect(nav).toBeInTheDocument();

    const main = document.getElementById("main");
    expect(main).not.toBeNull();
    expect(main?.tagName).toBe("MAIN");
    expect(within(main as HTMLElement).getByText("Page body")).toBeInTheDocument();
  });
});
