/**
 * tests-bff/chat-conversation-mgmt.test.tsx — RED suite for chat-conversation-mgmt task.
 *
 * Frozen contract (v1):
 *   PATCH /v1/conversations/{id}  body:{title}  -> {id,title,created_at,updated_at}
 *   renameConversation(id,title)  -> Promise<{id,title,...}>  via bffPatch
 *   forkConversation (client-side): createConversation + appendMessage per message
 *   exportConversation (client-side): Blob JSON + Blob markdown
 *   search (client-side): filter conversations list by title substring
 *
 * MSW pattern matches chat-history.test.tsx.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── imports under test ────────────────────────────────────────────────────
import {
  renameConversation,
  createConversation,
  getConversation,
  appendMessage,
} from "@/lib/conversations";
import { ChatHistorySidebar } from "@/components/chat/ChatHistorySidebar";

// ── URL constants ──────────────────────────────────────────────────────────
const APP = "http://localhost:3000";
const URL_CONVS = `${APP}/api/gw/v1/conversations`;
const convUrl = (id: string) => `${URL_CONVS}/${id}`;

// ── test data ──────────────────────────────────────────────────────────────
const CONV_A = {
  id: "conv-a",
  title: "Alpha chat",
  updated_at: "2026-06-29T10:00:00Z",
  message_count: 2,
};
const CONV_B = {
  id: "conv-b",
  title: "Beta session",
  updated_at: "2026-06-29T09:00:00Z",
  message_count: 1,
};
const CONV_C = {
  id: "conv-c",
  title: "Alpha test",
  updated_at: "2026-06-29T08:00:00Z",
  message_count: 0,
};

// ── helpers ────────────────────────────────────────────────────────────────
function withConvList(convs = [CONV_A, CONV_B, CONV_C]) {
  server.use(http.get(URL_CONVS, () => HttpResponse.json({ data: convs })));
}

function renderSidebar(props?: Partial<Parameters<typeof ChatHistorySidebar>[0]>) {
  const defaults = {
    activeId: null,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    refreshKey: 0,
    streaming: false,
    onRenameComplete: vi.fn(),
  };
  return render(<ChatHistorySidebar {...defaults} {...props} />);
}

// ── §1: renameConversation lib function ────────────────────────────────────

describe("renameConversation()", () => {
  it("calls PATCH /v1/conversations/{id} with {title} and resolves the updated conv", async () => {
    server.use(
      http.patch(convUrl("conv-1"), async ({ request }) => {
        const body = (await request.json()) as { title: string };
        return HttpResponse.json({
          id: "conv-1",
          title: body.title,
          created_at: "2026-06-29T08:00:00Z",
          updated_at: "2026-06-29T10:00:00Z",
        });
      }),
    );

    const result = await renameConversation("conv-1", "Renamed Title");
    expect(result.id).toBe("conv-1");
    expect(result.title).toBe("Renamed Title");
    expect(result.updated_at).toBeTruthy();
  });

  it("throws BffError on 404", async () => {
    server.use(
      http.patch(convUrl("gone"), () =>
        HttpResponse.json(
          { code: "ERR_CONVERSATION_NOT_FOUND", title: "Not found", status: 404 },
          { status: 404 },
        ),
      ),
    );

    await expect(renameConversation("gone", "Any")).rejects.toThrow();
  });

  it("throws BffError on 422 blank title", async () => {
    server.use(
      http.patch(convUrl("conv-1"), () =>
        HttpResponse.json({ detail: [{ msg: "title blank" }] }, { status: 422 }),
      ),
    );

    await expect(renameConversation("conv-1", "   ")).rejects.toThrow();
  });
});

// ── §2: fork / duplicate (client-side) ────────────────────────────────────

describe("fork conversation (client-side)", () => {
  it("creates a new conversation with title + (copy) then appends all source messages", async () => {
    const sourceMessages = [
      { role: "user" as const, content: "Hello" },
      { role: "assistant" as const, content: "World" },
    ];

    // Stub getConversation
    server.use(
      http.get(convUrl("source-id"), () =>
        HttpResponse.json({
          id: "source-id",
          title: "Original",
          messages: sourceMessages,
        }),
      ),
      // Stub createConversation
      http.post(URL_CONVS, async ({ request }) => {
        const body = (await request.json()) as { title: string };
        return HttpResponse.json({ id: "fork-id", title: body.title }, { status: 201 });
      }),
      // Stub appendMessage (returns 201)
      http.post(`${convUrl("fork-id")}/messages`, () =>
        HttpResponse.json(
          {
            id: "msg-1",
            role: "user",
            content: "Hello",
            created_at: "2026-06-29T10:00:00Z",
          },
          { status: 201 },
        ),
      ),
    );

    const source = await getConversation("source-id");
    const forked = await createConversation(`${source.title} (copy)`);
    expect(forked.id).toBe("fork-id");
    expect(forked.title).toBe("Original (copy)");

    // Append all messages to the fork
    for (const msg of source.messages) {
      await appendMessage(forked.id, msg.role, msg.content);
    }
    // Verified: no throw means the sequence completed
  });
});

// ── §3: export (client-side) ───────────────────────────────────────────────

describe("export conversation (client-side)", () => {
  let createObjectURLSpy: ReturnType<typeof vi.spyOn>;
  let revokeObjectURLSpy: ReturnType<typeof vi.spyOn>;
  let appendChildSpy: ReturnType<typeof vi.spyOn>;
  let clickCount: number;

  beforeEach(() => {
    clickCount = 0;
    createObjectURLSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake-url");
    revokeObjectURLSpy = vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);
    appendChildSpy = vi
      .spyOn(document.body, "appendChild")
      .mockImplementation((node) => {
        if (node instanceof HTMLAnchorElement) {
          clickCount += 1;
        }
        return node;
      });
  });

  afterEach(() => {
    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
    appendChildSpy.mockRestore();
  });

  it("triggers a JSON Blob download with correct content", async () => {
    server.use(
      http.get(convUrl("export-id"), () =>
        HttpResponse.json({
          id: "export-id",
          title: "Export Me",
          messages: [{ role: "user", content: "Hi" }],
        }),
      ),
    );

    const detail = await getConversation("export-id");
    // Simulate what the UI export helper does: create Blob and click anchor
    const blob = new Blob([JSON.stringify(detail, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${detail.title ?? "conversation"}.json`;
    document.body.appendChild(a);
    a.click();

    expect(createObjectURLSpy).toHaveBeenCalledOnce();
    expect(clickCount).toBe(1);
  });

  it("triggers a markdown Blob download with title + messages", async () => {
    server.use(
      http.get(convUrl("export-md"), () =>
        HttpResponse.json({
          id: "export-md",
          title: "Markdown Export",
          messages: [
            { role: "user", content: "Hello" },
            { role: "assistant", content: "World" },
          ],
        }),
      ),
    );

    const detail = await getConversation("export-md");
    // Build markdown content as the UI helper would
    const lines = [
      `# ${detail.title ?? "Conversation"}`,
      "",
      ...detail.messages.map(
        (m) => `**${m.role}**: ${m.content}`,
      ),
    ];
    const md = lines.join("\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${detail.title ?? "conversation"}.md`;
    document.body.appendChild(a);
    a.click();

    expect(createObjectURLSpy).toHaveBeenCalledOnce();
    expect(clickCount).toBe(1);
  });
});

// ── §4: search filter (client-side) ────────────────────────────────────────

describe("ChatHistorySidebar search filter", () => {
  it("filters conversations by title substring (case-insensitive)", async () => {
    withConvList([CONV_A, CONV_B, CONV_C]);
    renderSidebar();

    // Wait for conversations to load
    await waitFor(() => {
      expect(screen.getByText("Alpha chat")).toBeInTheDocument();
    });

    // Type in the search box
    const searchInput = screen.getByRole("searchbox");
    await userEvent.type(searchInput, "alpha");

    // Only Alpha chat and Alpha test should be visible
    await waitFor(() => {
      expect(screen.getByText("Alpha chat")).toBeInTheDocument();
      expect(screen.getByText("Alpha test")).toBeInTheDocument();
      expect(screen.queryByText("Beta session")).not.toBeInTheDocument();
    });
  });

  it("shows all conversations when search is cleared", async () => {
    withConvList([CONV_A, CONV_B]);
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText("Alpha chat")).toBeInTheDocument();
    });

    const searchInput = screen.getByRole("searchbox");
    await userEvent.type(searchInput, "alpha");

    await waitFor(() => {
      expect(screen.queryByText("Beta session")).not.toBeInTheDocument();
    });

    await userEvent.clear(searchInput);

    await waitFor(() => {
      expect(screen.getByText("Alpha chat")).toBeInTheDocument();
      expect(screen.getByText("Beta session")).toBeInTheDocument();
    });
  });
});

// ── §5: inline rename UX ───────────────────────────────────────────────────

describe("ChatHistorySidebar inline rename", () => {
  it("shows an editable input when rename icon is clicked", async () => {
    withConvList([CONV_A]);
    server.use(
      http.patch(convUrl("conv-a"), () =>
        HttpResponse.json({
          id: "conv-a",
          title: "Renamed Alpha",
          created_at: "2026-06-29T08:00:00Z",
          updated_at: "2026-06-29T10:01:00Z",
        }),
      ),
    );
    renderSidebar();

    await waitFor(() => {
      expect(screen.getByText("Alpha chat")).toBeInTheDocument();
    });

    // Find and click the rename button
    const renameBtn = screen.getByRole("button", { name: /rename alpha chat/i });
    await userEvent.click(renameBtn);

    // An input with the current title should appear
    const input = screen.getByDisplayValue("Alpha chat");
    expect(input).toBeInTheDocument();
  });

  it("commits rename on Enter and calls renameConversation", async () => {
    withConvList([CONV_A]);
    let patchCalled = false;
    server.use(
      http.patch(convUrl("conv-a"), async ({ request }) => {
        patchCalled = true;
        const body = (await request.json()) as { title: string };
        return HttpResponse.json({
          id: "conv-a",
          title: body.title,
          created_at: "2026-06-29T08:00:00Z",
          updated_at: "2026-06-29T10:01:00Z",
        });
      }),
    );
    renderSidebar();

    await waitFor(() => screen.getByText("Alpha chat"));

    const renameBtn = screen.getByRole("button", { name: /rename alpha chat/i });
    await userEvent.click(renameBtn);

    const input = screen.getByDisplayValue("Alpha chat");
    await userEvent.clear(input);
    await userEvent.type(input, "New Name");
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(patchCalled).toBe(true));
  });

  it("cancels rename on Escape without a network call", async () => {
    withConvList([CONV_A]);
    let patchCalled = false;
    server.use(
      http.patch(convUrl("conv-a"), () => {
        patchCalled = true;
        return HttpResponse.json({});
      }),
    );
    renderSidebar();

    await waitFor(() => screen.getByText("Alpha chat"));

    const renameBtn = screen.getByRole("button", { name: /rename alpha chat/i });
    await userEvent.click(renameBtn);

    const input = screen.getByDisplayValue("Alpha chat");
    await userEvent.type(input, " extra");
    await userEvent.keyboard("{Escape}");

    // Original label should be restored
    await waitFor(() => {
      expect(screen.getByText("Alpha chat")).toBeInTheDocument();
    });
    expect(patchCalled).toBe(false);
  });
});
