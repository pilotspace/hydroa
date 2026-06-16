/**
 * design-system-foundation — render contracts for the remaining shared primitives
 * (Card · Badge · Input · Table · Select). Asserts OBSERVABLE structure/behavior so
 * the foundation primitives are genuinely exercised (the surfaces consume them next),
 * not merely shipped. Honors M3's variant inventory.
 */
import { describe, it, expect, beforeAll, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
} from "@/components/ui/table";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { ChartCard, type ChartConfig } from "@/components/ui/chart";

describe("Card — composes header/title/description/content/footer", () => {
  it("renders all parts with their content", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Usage</CardTitle>
          <CardDescription>This month</CardDescription>
        </CardHeader>
        <CardContent>1,284 requests</CardContent>
        <CardFooter>Footer note</CardFooter>
      </Card>,
    );
    expect(screen.getByText("Usage")).toBeInTheDocument();
    expect(screen.getByText("This month")).toBeInTheDocument();
    expect(screen.getByText("1,284 requests")).toBeInTheDocument();
    expect(screen.getByText("Footer note")).toBeInTheDocument();
  });
});

// v24 — CardTitle gains an asChild (Radix Slot) escape hatch so a consumer can choose the heading
// level (keeping a correct document outline) without forking the CardTitle styling.
describe("CardTitle — asChild renders the caller's heading element", () => {
  it("test_cardtitle_aschild_renders_caller_heading", () => {
    render(
      <CardHeader>
        <CardTitle asChild>
          <h2>Sectioned title</h2>
        </CardTitle>
      </CardHeader>,
    );
    const h2 = screen.getByRole("heading", { level: 2, name: "Sectioned title" });
    expect(h2.tagName).toBe("H2");
    // CardTitle styling is merged onto the caller element (Slot), not lost
    expect(h2).toHaveClass("font-semibold");
  });

  it("test_cardtitle_default_is_h3", () => {
    render(<CardTitle>Plain title</CardTitle>);
    // default (no asChild) is byte-identical to today: an <h3>
    expect(screen.getByRole("heading", { level: 3, name: "Plain title" })).toBeInTheDocument();
  });
});

// v24 — ChartCard gains headingLevel?: 2 | 3 (default 3) so a card sitting directly under the page
// <h1> can opt its title up to <h2> and keep the outline skip-free.
describe("ChartCard — headingLevel opt-in", () => {
  const config: ChartConfig = { requests: { label: "Requests", color: "var(--color-chart-1)" } };

  it("test_chartcard_headinglevel_2", () => {
    render(
      <ChartCard title="Usage over time" config={config} headingLevel={2}>
        <div data-testid="body" />
      </ChartCard>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Usage over time" })).toBeInTheDocument();
  });

  it("test_chartcard_default_headinglevel_is_3", () => {
    render(
      <ChartCard title="Defaulted" config={config}>
        <div data-testid="body" />
      </ChartCard>,
    );
    // default is byte-identical to today: <h3>
    expect(screen.getByRole("heading", { level: 3, name: "Defaulted" })).toBeInTheDocument();
  });
});

describe("Badge — every variant renders the label", () => {
  it.each(["default", "secondary", "outline", "success", "warning", "destructive"] as const)(
    "variant %s",
    (variant) => {
      render(<Badge variant={variant}>{`v-${variant}`}</Badge>);
      expect(screen.getByText(`v-${variant}`)).toBeInTheDocument();
    },
  );
});

describe("Input — is a native input that accepts typing", () => {
  it("renders and is typeable", async () => {
    render(<Input placeholder="Monthly budget" aria-label="budget" />);
    const input = screen.getByLabelText("budget");
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveAttribute("placeholder", "Monthly budget");
    await userEvent.type(input, "50");
    expect(input).toHaveValue("50");
  });
});

describe("Table — renders rows, header scope, and caption", () => {
  it("composes a semantic table", () => {
    render(
      <Table>
        <TableCaption>Recent usage</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead>Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>openai/gpt-4o</TableCell>
            <TableCell>$1.23</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    expect(screen.getByText("Recent usage")).toBeInTheDocument();
    const colHeader = screen.getByRole("columnheader", { name: "Model" });
    expect(colHeader).toHaveAttribute("scope", "col");
    expect(screen.getByRole("cell", { name: "openai/gpt-4o" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });
});

describe("Select — Radix select opens and selects (keyboard/pointer)", () => {
  beforeAll(() => {
    // jsdom lacks the pointer/scroll/observer APIs Radix Select uses when opening.
    if (!Element.prototype.hasPointerCapture) {
      Element.prototype.hasPointerCapture = vi.fn(() => false);
    }
    Element.prototype.scrollIntoView = vi.fn();
    if (!("ResizeObserver" in globalThis)) {
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }
  });

  it("renders a trigger and reveals options on open", async () => {
    render(
      <Select>
        <SelectTrigger aria-label="window">
          <SelectValue placeholder="Pick a window" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="day">Day</SelectItem>
          <SelectItem value="week">Week</SelectItem>
        </SelectContent>
      </Select>,
    );
    const trigger = screen.getByRole("combobox", { name: "window" });
    expect(trigger).toBeInTheDocument();
    await userEvent.click(trigger);
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByText("Day")).toBeInTheDocument();
    expect(within(listbox).getByText("Week")).toBeInTheDocument();
  });
});
