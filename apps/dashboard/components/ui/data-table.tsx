"use client";

import * as React from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "./table";
import { Empty } from "./states";

/**
 * DataTable — a generic, sortable table over the v13 Table primitives, powered by
 * @tanstack/react-table. Sortable headers render as keyboard-operable buttons; zero rows
 * render the shared Empty state. Token-only styling (R3).
 */
export interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  caption?: string;
  emptyMessage?: string;
  className?: string;
  /** Accessible name forwarded to the underlying <table> (wins over caption for getByRole name). */
  ariaLabel?: string;
}

export function DataTable<TData, TValue>({
  columns,
  data,
  caption,
  emptyMessage = "No results.",
  className,
  ariaLabel,
}: DataTableProps<TData, TValue>) {
  const [sorting, setSorting] = React.useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (data.length === 0) {
    return <Empty title={emptyMessage} />;
  }

  return (
    <Table data-slot="data-table" aria-label={ariaLabel} className={className}>
      {caption ? <TableCaption>{caption}</TableCaption> : null}
      <TableHeader>
        {table.getHeaderGroups().map((group) => (
          <TableRow key={group.id}>
            {group.headers.map((header) => {
              const canSort = header.column.getCanSort();
              const sorted = header.column.getIsSorted();
              const content = header.isPlaceholder
                ? null
                : flexRender(header.column.columnDef.header, header.getContext());
              if (!canSort) return <TableHead key={header.id}>{content}</TableHead>;
              const SortIcon = sorted === "asc" ? ArrowUp : sorted === "desc" ? ArrowDown : ChevronsUpDown;
              return (
                <TableHead key={header.id} aria-sort={sorted ? (sorted === "asc" ? "ascending" : "descending") : "none"}>
                  <button
                    type="button"
                    onClick={header.column.getToggleSortingHandler()}
                    className="-ml-1 inline-flex items-center gap-1 rounded px-1 py-0.5 font-medium hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {content}
                    <SortIcon className="size-3.5" aria-hidden="true" />
                  </button>
                </TableHead>
              );
            })}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => (
          <TableRow key={row.id}>
            {row.getVisibleCells().map((cell) => (
              <TableCell key={cell.id}>
                {cell.column.columnDef.cell
                  ? flexRender(cell.column.columnDef.cell, cell.getContext())
                  : String(cell.getValue() ?? "")}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
