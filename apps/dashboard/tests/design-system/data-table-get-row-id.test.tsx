/**
 * tests/design-system/data-table-get-row-id.test.tsx — RED/GREEN for the DataTable
 * `getRowId` prop (audit-remediation, Dashboard item 1: TeamBudgetForm stale-save).
 *
 * DataTable's row `key` defaulted to tanstack's own default `getRowId`, which is the
 * row's ARRAY INDEX. A row that hosts UNCONTROLLED local state (e.g. an inline edit
 * form) is then keyed by POSITION, not by identity: when the underlying data array
 * shrinks/reorders (e.g. a row at an earlier position is removed), React's keyed
 * reconciliation matches the surviving position's key to whichever component
 * PREVIOUSLY occupied it — reusing that fiber (and its stale local state) to render
 * the DIFFERENT row now at that position, instead of mounting a fresh instance.
 *
 * This is a deterministic, synchronous proof against DataTable directly (no network/
 * React Query layer) — `rerender()` swaps the `data` array exactly like a real data
 * change would. `getRowId` is OPT-IN: omitted, behavior is byte-identical to before
 * (every other DataTable caller is unaffected — verified separately via
 * mcp__serena find_referencing_symbols across all 18 current call sites, none of
 * which pass `getRowId`).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/ui/data-table";

interface Row {
  id: string;
  name: string;
  value: string;
}

/** A minimal stand-in for TeamBudgetForm: local draft state seeded from the row prop. */
function StatefulCell({ row }: { row: Row }) {
  const [draft] = useState(row.value);
  return <span data-testid={`draft-${row.name}`}>{draft}</span>;
}

const COLUMNS: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Name" },
  { id: "draft", header: "Draft", cell: ({ row }) => <StatefulCell row={row.original} /> },
];

const A: Row = { id: "a", name: "A", value: "100.00" };
const B: Row = { id: "b", name: "B", value: "25.00" };
const C: Row = { id: "c", name: "C", value: "50.00" };

describe("DataTable — getRowId (stale-reuse fix)", () => {
  it("test_without_getRowId_a_shrunk_array_reuses_a_stale_component_instance", () => {
    // RED-documenting case: pins tanstack's own documented default (index-keyed) so a
    // future tanstack upgrade that changes this default is caught here, not silently
    // in production. Proves the DEFECT MECHANISM this fix defends against.
    const { rerender } = render(<DataTable columns={COLUMNS} data={[A, B, C]} />);
    expect(screen.getByTestId("draft-C").textContent).toBe("50.00");

    // Remove A (the first row) — B and C both shift up one array position; only
    // position "0" survives in the new (length-1) array.
    rerender(<DataTable columns={COLUMNS} data={[C]} />);

    // Without getRowId, position "0" is reused from A's own stale fiber (draft
    // seeded "100.00", never touched) — C's row incorrectly shows A's old value.
    expect(screen.getByTestId("draft-C").textContent).toBe("100.00");
  });

  it("test_with_getRowId_identity_survives_reorder_no_stale_reuse", () => {
    const { rerender } = render(
      <DataTable columns={COLUMNS} data={[A, B, C]} getRowId={(r) => r.id} />,
    );
    expect(screen.getByTestId("draft-C").textContent).toBe("50.00");

    rerender(<DataTable columns={COLUMNS} data={[C]} getRowId={(r) => r.id} />);

    // C keeps its OWN identity-keyed instance — never reuses A's stale one.
    expect(screen.getByTestId("draft-C").textContent).toBe("50.00");
  });

  it("test_getRowId_omitted_is_backward_compatible_default_render", () => {
    // Every existing DataTable caller omits getRowId — confirms the prop is
    // additive-only and default behavior (single-row render) is unchanged.
    render(<DataTable columns={COLUMNS} data={[A]} />);
    expect(screen.getByTestId("draft-A").textContent).toBe("100.00");
  });
});
