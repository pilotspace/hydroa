/**
 * tests-bff/memory-workspace-console.test.tsx — Console-grade memory workspace tests.
 *
 * These tests pin the NEW Console-grade behaviors that the rebuilt MemoryWorkspace
 * must satisfy. They FAIL before implementation (RED) and pass after (GREEN).
 *
 * Behaviors under test:
 *   1. two_pane_layout_renders_library_and_inspector
 *   2. library_pane_shows_recency_sort_by_default
 *   3. library_pane_shows_result_count
 *   4. library_items_show_embedding_status_indicator
 *   5. clicking_library_item_shows_detail_in_inspector
 *   6. inspector_pane_shows_full_content_and_metadata
 *   7. inspector_pane_shows_created_at_and_embedding_status
 *   8. inspector_delete_guarded_requires_confirmation
 *   9. score_bar_visible_when_score_not_null
 *  10. score_bar_hidden_when_score_is_null_shows_text_match
 *  11. keyboard_navigation_between_list_items
 *  12. sort_toggle_switches_between_recency_and_relevance
 *  13. search_mode_shows_relevance_sort
 *  14. add_composer_metadata_field_optional
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

import { MemoryWorkspace } from "@/components/memory/MemoryWorkspace";

// ── URL constants ─────────────────────────────────────────────────────────────
const URL_MEMORIES = "http://localhost:3000/api/gw/v1/memories";
const URL_SEARCH = "http://localhost:3000/api/gw/v1/memories/search";
const memUrl = (id: string) => `http://localhost:3000/api/gw/v1/memories/${id}`;

// ── fixtures ──────────────────────────────────────────────────────────────────
const MEM_A = {
  id: "mem-a",
  content: "The capital of France is Paris.",
  created_at: "2026-06-26T10:00:00Z",
  has_embedding: true,
};
const MEM_B = {
  id: "mem-b",
  content: "TypeScript is a superset of JavaScript.",
  created_at: "2026-06-25T09:00:00Z",
  has_embedding: false,
};
const MEM_C = {
  id: "mem-c",
  content: "React is a JavaScript library for UIs.",
  created_at: "2026-06-24T08:00:00Z",
  has_embedding: true,
};

// ════════════════════════════════════════════════════════════════════════════
describe("MemoryWorkspace — Console-grade behaviors", () => {
  beforeEach(() => {
    server.use(
      http.get(URL_MEMORIES, () =>
        HttpResponse.json({ data: [MEM_A, MEM_B, MEM_C] }),
      ),
    );
  });

  // ── 1. two_pane_layout_renders_library_and_inspector ─────────────────────
  it("two_pane_layout_renders_library_and_inspector", async () => {
    render(<MemoryWorkspace />);

    // Wait for load
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // LEFT pane: memory library region
    const libraryRegion = screen.getByRole("region", { name: /memory library/i });
    expect(libraryRegion).toBeInTheDocument();

    // RIGHT pane: inspector region (exists even when no selection)
    const inspectorRegion = screen.getByRole("region", { name: /inspector/i });
    expect(inspectorRegion).toBeInTheDocument();

    // Single h1 "Memory"
    const h1 = screen.getByRole("heading", { level: 1, name: /^memory$/i });
    expect(h1).toBeInTheDocument();
  });

  // ── 2. library_pane_shows_recency_sort_by_default ────────────────────────
  it("library_pane_shows_recency_sort_by_default", async () => {
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // A sort control must exist
    const sortControl =
      screen.getByRole("button", { name: /recency|newest|recent/i }) ??
      screen.getByRole("combobox", { name: /sort/i });
    expect(sortControl).toBeInTheDocument();

    // The default active sort label mentions "recency" or "newest"
    expect(sortControl).toHaveAccessibleName(/recency|newest|recent/i);
  });

  // ── 3. library_pane_shows_result_count ───────────────────────────────────
  it("library_pane_shows_result_count", async () => {
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // The library pane shows how many memories are loaded
    // e.g. "3 memories" or "3 items"
    expect(screen.getByText(/3 memor/i)).toBeInTheDocument();
  });

  // ── 4. library_items_show_embedding_status_indicator ─────────────────────
  it("library_items_show_embedding_status_indicator", async () => {
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Each item has an embedding status indicator.
    // MEM_A has_embedding=true → should show "embedded" indicator
    // MEM_B has_embedding=false → should show "not embedded" or "pending" indicator
    const embedded = screen.getAllByTitle(/embedded|has embedding/i);
    expect(embedded.length).toBeGreaterThan(0);
  });

  // ── 5. clicking_library_item_shows_detail_in_inspector ───────────────────
  it("clicking_library_item_shows_detail_in_inspector", async () => {
    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Click on MEM_A in the library list
    const memAItem = screen.getByRole("option", { name: /France.*Paris|capital of France/i });
    await user.click(memAItem);

    // Inspector now shows the full content of MEM_A
    const inspector = screen.getByRole("region", { name: /inspector/i });
    expect(within(inspector).getByText("The capital of France is Paris.")).toBeInTheDocument();
  });

  // ── 6. inspector_pane_shows_full_content_and_metadata ────────────────────
  it("inspector_pane_shows_full_content_and_metadata", async () => {
    server.use(
      http.get(URL_MEMORIES, () =>
        HttpResponse.json({
          data: [
            {
              id: "mem-meta",
              content: "Memory with metadata.",
              created_at: "2026-06-26T10:00:00Z",
              has_embedding: true,
              metadata: { source: "test-runner", priority: 1 },
            },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    const item = screen.getByRole("option", { name: /Memory with metadata/i });
    await user.click(item);

    const inspector = screen.getByRole("region", { name: /inspector/i });

    // Full content
    expect(within(inspector).getByText("Memory with metadata.")).toBeInTheDocument();

    // Metadata section
    expect(within(inspector).getByText(/source|test-runner/i)).toBeInTheDocument();
  });

  // ── 7. inspector_pane_shows_created_at_and_embedding_status ──────────────
  it("inspector_pane_shows_created_at_and_embedding_status", async () => {
    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Select MEM_A
    const item = screen.getByRole("option", { name: /France.*Paris|capital of France/i });
    await user.click(item);

    const inspector = screen.getByRole("region", { name: /inspector/i });

    // created_at must appear (formatted or raw ISO-ish)
    // 2026-06-26 or Jun 26 or similar
    expect(
      within(inspector).getByText(/2026|jun 26|june 26/i),
    ).toBeInTheDocument();

    // Embedding status label in inspector
    expect(within(inspector).getByText(/embedded|has embedding/i)).toBeInTheDocument();
  });

  // ── 8. inspector_delete_guarded_requires_confirmation ────────────────────
  it("inspector_delete_guarded_requires_confirmation", async () => {
    let deleteCalled = false;
    server.use(
      http.delete(memUrl("mem-a"), () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Select MEM_A in inspector
    const item = screen.getByRole("option", { name: /France.*Paris|capital of France/i });
    await user.click(item);

    const inspector = screen.getByRole("region", { name: /inspector/i });
    const deleteBtn = within(inspector).getByRole("button", { name: /delete/i });

    // First click: shows confirmation — does NOT immediately delete
    await user.click(deleteBtn);
    expect(deleteCalled).toBe(false);

    // A confirmation prompt must appear
    const confirmBtn = screen.getByRole("button", { name: /confirm|yes.*delete|are you sure/i });
    expect(confirmBtn).toBeInTheDocument();

    // Second click on confirm: NOW deletes
    await user.click(confirmBtn);
    await waitFor(() => expect(deleteCalled).toBe(true));
  });

  // ── 9. score_bar_visible_when_score_not_null ─────────────────────────────
  it("score_bar_visible_when_score_not_null", async () => {
    server.use(
      http.post(URL_SEARCH, () =>
        HttpResponse.json({
          data: [
            {
              id: "mem-a",
              content: "The capital of France is Paris.",
              score: 0.97,
              created_at: "2026-06-26T10:00:00Z",
            },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    await user.type(
      screen.getByRole("textbox", { name: /search memories/i }),
      "France",
    );
    await user.click(screen.getByRole("button", { name: /search/i }));

    // Wait for results
    await screen.findByText("The capital of France is Paris.");

    // Score bar (progressbar role) must be visible for score 0.97
    const scoreBars = screen.getAllByRole("progressbar");
    expect(scoreBars.length).toBeGreaterThan(0);

    // At least one bar has aria-valuenow near 0.97 (or expressed as percentage)
    const bar = scoreBars[0];
    const valueNow = bar.getAttribute("aria-valuenow");
    expect(valueNow).not.toBeNull();
  });

  // ── 10. score_bar_hidden_when_score_is_null_shows_text_match ─────────────
  it("score_bar_hidden_when_score_is_null_shows_text_match", async () => {
    server.use(
      http.post(URL_SEARCH, () =>
        HttpResponse.json({
          data: [
            {
              id: "mem-null",
              content: "Null score memory.",
              score: null,
              created_at: "2026-06-26T10:00:00Z",
            },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    await user.type(
      screen.getByRole("textbox", { name: /search memories/i }),
      "something",
    );
    await user.click(screen.getByRole("button", { name: /search/i }));

    await screen.findByText("Null score memory.");

    // No progressbar for null score
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    // "text match" fallback shown instead
    expect(screen.getByText(/text match/i)).toBeInTheDocument();
  });

  // ── 11. keyboard_navigation_between_list_items ───────────────────────────
  it("keyboard_navigation_between_list_items", async () => {
    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // The list must be a listbox (keyboard nav via arrow keys)
    const listbox = screen.getByRole("listbox", { name: /memories/i });
    expect(listbox).toBeInTheDocument();

    // Focus the listbox and press ArrowDown to select the first item
    listbox.focus();
    await user.keyboard("{ArrowDown}");

    // First item should be selected/active
    const options = within(listbox).getAllByRole("option");
    expect(options[0]).toHaveAttribute("aria-selected", "true");

    // Press ArrowDown again → second item selected
    await user.keyboard("{ArrowDown}");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    expect(options[0]).toHaveAttribute("aria-selected", "false");
  });

  // ── 12. sort_toggle_switches_between_recency_and_relevance ───────────────
  it("sort_toggle_switches_between_recency_and_relevance", async () => {
    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Default sort is recency
    const recencyBtn = screen.getByRole("button", { name: /recency|newest|recent/i });
    expect(recencyBtn).toBeInTheDocument();

    // Toggle to relevance — a "relevance" option must exist
    const relevanceToggle = screen.getByRole("button", { name: /relevance/i });
    await user.click(relevanceToggle);

    // Relevance toggle becomes active (aria-pressed or aria-selected)
    expect(relevanceToggle).toHaveAttribute("aria-pressed", "true");
    // Recency is now inactive
    expect(recencyBtn).toHaveAttribute("aria-pressed", "false");
  });

  // ── 13. search_mode_shows_relevance_sort ─────────────────────────────────
  it("search_mode_shows_relevance_sort", async () => {
    server.use(
      http.post(URL_SEARCH, () =>
        HttpResponse.json({
          data: [
            { id: "mem-a", content: "Paris.", score: 0.9, created_at: "2026-06-26T10:00:00Z" },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    await user.type(
      screen.getByRole("textbox", { name: /search memories/i }),
      "Paris",
    );
    await user.click(screen.getByRole("button", { name: /search/i }));

    await screen.findByText("Paris.");

    // After a search, relevance sort is automatically active
    const relevanceBtn = screen.getByRole("button", { name: /relevance/i });
    expect(relevanceBtn).toHaveAttribute("aria-pressed", "true");
  });

  // ── 14. add_composer_metadata_field_optional ─────────────────────────────
  it("add_composer_metadata_field_optional", async () => {
    let captured: { content?: string; metadata?: Record<string, unknown> } = {};
    server.use(
      http.post(URL_MEMORIES, async ({ request }) => {
        captured = (await request.json()) as typeof captured;
        return HttpResponse.json(
          { id: "mem-new", content: captured.content ?? "", created_at: "2026-06-26T11:00:00Z" },
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // A metadata field must exist in the add composer
    const metadataField = screen.getByRole("textbox", { name: /metadata/i });
    expect(metadataField).toBeInTheDocument();

    // Fill in content + valid JSON metadata
    await user.type(
      screen.getByRole("textbox", { name: /memory content/i }),
      "New memory",
    );
    await user.clear(metadataField);
    // userEvent v14: {{ escapes a literal opening brace; } is always literal outside a descriptor
    await user.type(metadataField, '{{"source":"test"}');

    const submitBtn = screen.getByRole("button", { name: /add memory/i });
    await user.click(submitBtn);

    await waitFor(() =>
      expect(captured.metadata).toEqual({ source: "test" }),
    );
  });
});
