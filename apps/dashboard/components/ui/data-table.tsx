"use client";

import * as React from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { fuzzySearch } from "@/lib/fuzzy";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "./table";
import { Input } from "./input";
import { Empty } from "./states";

/**
 * DataTable — a generic, sortable table over the v13 Table primitives, powered by
 * @tanstack/react-table. Sortable headers render as keyboard-operable buttons; zero rows
 * render the shared Empty state. Token-only styling (R3).
 *
 * Pagination + fuzzy search are OPT-IN (v54): with neither `searchable` nor
 * `pageSizeOptions` set, behavior is byte-identical to before — so the 4 other callers
 * (Usage·Alerts·Audit·Upstreams) are untouched. When `searchable`, a labeled search box
 * derives a Fuse.js-ranked working set (lib/fuzzy.ts) — a non-empty query that matches
 * nothing falls to the shared Empty (search box retained). When `pageSizeOptions`, the
 * table paginates with a "Rows per page" control + Previous/Next + a "Page X of Y" status.
 */
export interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  caption?: string;
  emptyMessage?: string;
  className?: string;
  /** Accessible name forwarded to the underlying <table> (wins over caption for getByRole name). */
  ariaLabel?: string;
  /** When true, render a labeled fuzzy-search box above the table. */
  searchable?: boolean;
  /** Placeholder + accessible name for the search box (default "Search…"). */
  searchPlaceholder?: string;
  /** Fields the fuzzy filter reads — required when `searchable`. */
  searchKeys?: (keyof TData)[];
  /** Empty-state title shown when a non-empty query matches nothing (default "No matches found."). */
  searchEmptyMessage?: string;
  /** When set, enable client pagination + a "Rows per page" control; [0] = initial page size. */
  pageSizeOptions?: number[];
}

export function DataTable<TData, TValue>({
  columns,
  data,
  caption,
  emptyMessage = "No results.",
  className,
  ariaLabel,
  searchable = false,
  searchPlaceholder = "Search…",
  searchKeys,
  searchEmptyMessage = "No matches found.",
  pageSizeOptions,
}: DataTableProps<TData, TValue>) {
  const [sorting, setSorting] = React.useState<SortingState>([]);
  const [query, setQuery] = React.useState("");

  const paginated = pageSizeOptions !== undefined && pageSizeOptions.length > 0;
  const querying = searchable && query.trim() !== "";

  // The working set: Fuse-ranked when searching, otherwise the data unchanged. Memoized so
  // the table only re-resets its page index when the query (not every render) changes.
  const workingData = React.useMemo(() => {
    if (querying && searchKeys && searchKeys.length > 0) {
      return fuzzySearch(data, query, searchKeys);
    }
    return data;
  }, [data, querying, query, searchKeys]);

  const table = useReactTable({
    data: workingData,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    ...(paginated
      ? {
          getPaginationRowModel: getPaginationRowModel(),
          initialState: { pagination: { pageSize: pageSizeOptions[0] } },
          // Page-index reset on filter: changing the query swaps `workingData` (new ref) →
          // tanstack's autoResetPageIndex (default true) resets to page 0. The page-size
          // change handler resets explicitly (table.setPageIndex(0)) since it does not
          // change `data`. Both paths are covered by tests (search/page-size reset to "Page 1").
        }
      : {}),
  });

  const rows = table.getRowModel().rows;

  const tableMarkup = (
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
        {rows.map((row) => (
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

  // ── Default path: neither search nor pagination → byte-identical to the original. ──
  if (!searchable && !paginated) {
    if (data.length === 0) return <Empty title={emptyMessage} />;
    return tableMarkup;
  }

  // ── Enhanced path: search box (always visible) + table-or-Empty + pager. ──
  const pageCount = table.getPageCount();
  const pageIndex = table.getState().pagination.pageIndex;
  const bodyEmptyTitle = querying ? searchEmptyMessage : emptyMessage;

  return (
    <div className="flex flex-col gap-3">
      {searchable && (
        <Input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
          className="max-w-sm"
        />
      )}

      {rows.length === 0 ? <Empty title={bodyEmptyTitle} /> : tableMarkup}

      {paginated && rows.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 px-1 text-sm">
          <label className="flex items-center gap-2 text-muted-foreground">
            <span>Rows per page</span>
            <select
              aria-label="Rows per page"
              value={table.getState().pagination.pageSize}
              onChange={(e) => {
                table.setPageSize(Number(e.target.value));
                table.setPageIndex(0);
              }}
              className="rounded-md border border-input bg-background px-2 py-1 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {pageSizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>

          <div className="flex items-center gap-3">
            {/* aria-live announces page changes to AT WITHOUT role=status — that role is
                reserved for the transient query-loading spinner (four-state invariant:
                no role=status after load). aria-live is a property, not a role, so it does
                not trip getByRole("status"). */}
            <span aria-live="polite" className="text-muted-foreground">
              Page {pageIndex + 1} of {pageCount}
            </span>
            <button
              type="button"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="inline-flex items-center rounded-md border border-border bg-card px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="inline-flex items-center rounded-md border border-border bg-card px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
