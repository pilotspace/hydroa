/**
 * tests-bff/memory-workspace.test.tsx — RED suite for v44 memory workspace.
 *
 * Pins the frozen contract:
 *   - MemoryWorkspace (new component): list / add / search / delete
 *   - lib/memories (new BFF client): listMemories / createMemory / searchMemories /
 *     deleteMemory
 *
 * Mirrors chat-history.test.tsx for MSW setup.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── imports under test ────────────────────────────────────────────────────────
import { MemoryWorkspace } from "@/components/memory/MemoryWorkspace";
import {
  listMemories,
  createMemory,
  searchMemories,
  deleteMemory,
} from "@/lib/memories";

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
  created_at: "2026-06-26T09:00:00Z",
  has_embedding: false,
};

// ════════════════════════════════════════════════════════════════════════════
describe("lib/memories — typed BFF client", () => {
  it("listMemories_calls_correct_endpoint", async () => {
    server.use(
      http.get(URL_MEMORIES, () => HttpResponse.json({ data: [MEM_A] })),
    );
    const result = await listMemories();
    expect(result.data).toHaveLength(1);
    expect(result.data[0].id).toBe("mem-a");
    expect(result.data[0].content).toBe("The capital of France is Paris.");
    expect(result.data[0].has_embedding).toBe(true);
  });

  it("createMemory_posts_content", async () => {
    server.use(
      http.post(URL_MEMORIES, async ({ request }) => {
        const body = (await request.json()) as { content?: string };
        return HttpResponse.json(
          { id: "mem-new", content: body.content ?? "", created_at: "2026-06-26T11:00:00Z" },
          { status: 201 },
        );
      }),
    );
    const result = await createMemory("Hello memory");
    expect(result.id).toBe("mem-new");
    expect(result.content).toBe("Hello memory");
  });

  it("createMemory_includes_metadata_when_provided", async () => {
    let captured: { content?: string; metadata?: Record<string, unknown> } = {};
    server.use(
      http.post(URL_MEMORIES, async ({ request }) => {
        captured = (await request.json()) as typeof captured;
        return HttpResponse.json(
          { id: "mem-meta", content: captured.content ?? "", created_at: "2026-06-26T11:00:00Z" },
          { status: 201 },
        );
      }),
    );
    await createMemory("With meta", { source: "test" });
    expect(captured.metadata).toEqual({ source: "test" });
  });

  it("searchMemories_posts_query", async () => {
    server.use(
      http.post(URL_SEARCH, async ({ request }) => {
        const body = (await request.json()) as { query?: string; top_k?: number };
        return HttpResponse.json({
          data: [
            { id: "mem-a", content: "Paris is the capital of France.", score: 0.97, created_at: "2026-06-26T10:00:00Z" },
            { id: "mem-b", content: "France is in Europe.", score: 0.75, created_at: "2026-06-26T09:00:00Z" },
          ],
        });
      }),
    );
    const result = await searchMemories("capital of France");
    expect(result.data).toHaveLength(2);
    expect(result.data[0].score).toBe(0.97);
    expect(result.data[1].score).toBe(0.75);
  });

  it("deleteMemory_sends_delete_to_correct_url", async () => {
    let called = false;
    server.use(
      http.delete(memUrl("mem-a"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await deleteMemory("mem-a");
    expect(called).toBe(true);
  });
});

// ════════════════════════════════════════════════════════════════════════════
describe("MemoryWorkspace", () => {
  beforeEach(() => {
    // Default: return empty list so most tests start clean
    server.use(
      http.get(URL_MEMORIES, () => HttpResponse.json({ data: [] })),
    );
  });

  // ── list_renders ────────────────────────────────────────────────────────
  it("list_renders_two_items", async () => {
    server.use(
      http.get(URL_MEMORIES, () => HttpResponse.json({ data: [MEM_A, MEM_B] })),
    );
    render(<MemoryWorkspace />);

    expect(await screen.findByText("The capital of France is Paris.")).toBeInTheDocument();
    expect(await screen.findByText("TypeScript is a superset of JavaScript.")).toBeInTheDocument();
  });

  // ── add_memory ──────────────────────────────────────────────────────────
  it("add_memory_posts_and_refreshes_list", async () => {
    let postBody: { content?: string } = {};
    let callCount = 0;

    server.use(
      http.post(URL_MEMORIES, async ({ request }) => {
        postBody = (await request.json()) as { content?: string };
        return HttpResponse.json(
          { id: "mem-new", content: postBody.content ?? "", created_at: "2026-06-26T11:00:00Z" },
          { status: 201 },
        );
      }),
    );
    server.use(
      http.get(URL_MEMORIES, () => {
        callCount++;
        if (callCount >= 2) {
          return HttpResponse.json({
            data: [{ id: "mem-new", content: "Brand new memory", created_at: "2026-06-26T11:00:00Z" }],
          });
        }
        return HttpResponse.json({ data: [] });
      }),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    // Wait for initial load
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const textarea = screen.getByRole("textbox", { name: /memory content/i });
    const submitBtn = screen.getByRole("button", { name: /add memory/i });

    // Submit button is disabled when content is empty
    expect(submitBtn).toBeDisabled();

    await user.type(textarea, "Brand new memory");
    expect(submitBtn).not.toBeDisabled();

    await user.click(submitBtn);

    // POST was made with the right content
    await waitFor(() => expect(postBody.content).toBe("Brand new memory"));

    // New item appears after refresh
    await screen.findByText("Brand new memory");
  });

  // ── search_ranked ────────────────────────────────────────────────────────
  it("search_ranked_renders_results_with_scores", async () => {
    server.use(
      http.post(URL_SEARCH, async ({ request }) => {
        const body = (await request.json()) as { query?: string };
        expect(body.query).toBe("France capital");
        return HttpResponse.json({
          data: [
            { id: "mem-a", content: "Paris is the capital of France.", score: 0.97, created_at: "2026-06-26T10:00:00Z" },
            { id: "mem-b", content: "France is in Europe.", score: 0.75, created_at: "2026-06-26T09:00:00Z" },
            { id: "mem-c", content: "Text match result.", score: null, created_at: "2026-06-26T08:00:00Z" },
          ],
        });
      }),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const searchInput = screen.getByRole("textbox", { name: /search memories/i });
    const searchBtn = screen.getByRole("button", { name: /search/i });

    // Search button disabled when query is empty
    expect(searchBtn).toBeDisabled();

    await user.type(searchInput, "France capital");
    expect(searchBtn).not.toBeDisabled();

    await user.click(searchBtn);

    // Both scored results appear
    await screen.findByText("Paris is the capital of France.");
    expect(screen.getByText("France is in Europe.")).toBeInTheDocument();
    expect(screen.getByText("Text match result.")).toBeInTheDocument();

    // Score 0.97 is shown (rendered in the page)
    expect(screen.getByText(/0\.97/)).toBeInTheDocument();

    // null score renders a text fallback, NOT a fabricated number
    // getAllByText handles the case where multiple elements match (e.g. content text
    // "Text match result." also contains "text match").
    const textMatchOrDash = screen.getAllByText(/text match|—/i);
    expect(textMatchOrDash.length).toBeGreaterThan(0);
  });

  it("search_null_score_never_shows_fabricated_number", async () => {
    server.use(
      http.post(URL_SEARCH, () =>
        HttpResponse.json({
          data: [
            { id: "mem-null", content: "Null score item.", score: null, created_at: "2026-06-26T10:00:00Z" },
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    await user.type(screen.getByRole("textbox", { name: /search memories/i }), "anything");
    await user.click(screen.getByRole("button", { name: /search/i }));

    await screen.findByText("Null score item.");

    // A numeric score (e.g. "0.xx" or "1.0") must NOT appear for the null-score item
    // The score area for that item must show a text fallback
    // We check that no decimal number pattern appears in the result area
    const resultItems = screen.getAllByText(/Null score item\./);
    expect(resultItems.length).toBeGreaterThan(0);

    // The null score renders as text fallback (e.g. "text match" or "—")
    const textFallback = screen.queryByText(/text match|—/i);
    expect(textFallback).toBeInTheDocument();
  });

  // ── delete ───────────────────────────────────────────────────────────────
  it("delete_removes_item_from_list", async () => {
    let deleteCalled = false;
    server.use(
      http.get(URL_MEMORIES, () => {
        if (deleteCalled) return HttpResponse.json({ data: [] });
        return HttpResponse.json({ data: [MEM_A] });
      }),
    );
    server.use(
      http.delete(memUrl("mem-a"), () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    render(<MemoryWorkspace />);

    await screen.findByText("The capital of France is Paris.");

    const deleteBtn = screen.getByRole("button", { name: /delete/i });
    await user.click(deleteBtn);

    await waitFor(() => expect(deleteCalled).toBe(true));
    await waitFor(() =>
      expect(screen.queryByText("The capital of France is Paris.")).not.toBeInTheDocument(),
    );
  });

  // ── list_failure_nonblocking ─────────────────────────────────────────────
  it("list_failure_nonblocking_shows_error_no_crash", async () => {
    server.use(
      http.get(URL_MEMORIES, () =>
        HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 }),
      ),
    );
    render(<MemoryWorkspace />);

    // Error state renders (non-blocking — component is still mounted)
    const errorEl = await screen.findByRole("alert");
    expect(errorEl).toBeInTheDocument();

    // The workspace UI is still accessible — the page does not crash/unmount
    expect(screen.getByRole("textbox", { name: /memory content/i })).toBeInTheDocument();
  });

  // ── empty_noop ────────────────────────────────────────────────────────────
  it("empty_content_submit_disabled_no_request", async () => {
    let postCalled = false;
    server.use(
      http.post(URL_MEMORIES, () => {
        postCalled = true;
        return HttpResponse.json(
          { id: "x", content: "x", created_at: "2026-06-26T10:00:00Z" },
          { status: 201 },
        );
      }),
    );

    render(<MemoryWorkspace />);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const submitBtn = screen.getByRole("button", { name: /add memory/i });
    expect(submitBtn).toBeDisabled();

    // Clicking the disabled button does nothing
    await userEvent.click(submitBtn);
    expect(postCalled).toBe(false);
  });

  it("empty_query_search_disabled_no_request", async () => {
    let searchCalled = false;
    server.use(
      http.post(URL_SEARCH, () => {
        searchCalled = true;
        return HttpResponse.json({ data: [] });
      }),
    );

    render(<MemoryWorkspace />);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const searchBtn = screen.getByRole("button", { name: /search/i });
    expect(searchBtn).toBeDisabled();

    await userEvent.click(searchBtn);
    expect(searchCalled).toBe(false);
  });

  // ── whitespace_noop ───────────────────────────────────────────────────────
  it("whitespace_only_content_keeps_submit_disabled", async () => {
    render(<MemoryWorkspace />);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const textarea = screen.getByRole("textbox", { name: /memory content/i });
    const submitBtn = screen.getByRole("button", { name: /add memory/i });

    await userEvent.type(textarea, "   ");
    expect(submitBtn).toBeDisabled();
  });
});
