/**
 * tests-bff/artifacts-workspace.test.tsx — RED suite for v45 artifacts workspace.
 *
 * Pins the frozen contract:
 *   - ArtifactsWorkspace (new component): list / upload / download / delete
 *   - lib/artifacts (new BFF client): listArtifacts / createArtifact /
 *     downloadArtifact / deleteArtifact
 *
 * Extended for console-grade IA (v54):
 *   - search/filter by name + type
 *   - sort by name / size / date
 *   - detail pane with metadata + preview (image / text / json)
 *   - drag-and-drop upload
 *   - delete confirmation guard
 *
 * Mirrors memory-workspace.test.tsx for MSW setup.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── imports under test ────────────────────────────────────────────────────────
import { ArtifactsWorkspace } from "@/components/artifacts/ArtifactsWorkspace";
import {
  listArtifacts,
  createArtifact,
  downloadArtifact,
  deleteArtifact,
  type ArtifactItem,
} from "@/lib/artifacts";

// ── URL constants ─────────────────────────────────────────────────────────────
const URL_ARTIFACTS = "http://localhost:3000/api/gw/v1/artifacts";
const artUrl = (id: string) => `http://localhost:3000/api/gw/v1/artifacts/${id}`;

// ── fixtures ──────────────────────────────────────────────────────────────────
const ART_A: ArtifactItem = {
  id: "art-a",
  name: "report.pdf",
  content_type: "application/pdf",
  size_bytes: 1024,
  created_at: "2026-06-26T10:00:00Z",
};
const ART_B: ArtifactItem = {
  id: "art-b",
  name: "data.csv",
  content_type: "text/csv",
  size_bytes: 512,
  created_at: "2026-06-26T09:00:00Z",
};

// ── console-grade test fixtures ───────────────────────────────────────────────
const ART_IMG: ArtifactItem = {
  id: "art-img",
  name: "photo.png",
  content_type: "image/png",
  size_bytes: 2048,
  created_at: "2026-06-26T11:00:00Z",
};

const ART_TXT: ArtifactItem = {
  id: "art-txt",
  name: "notes.txt",
  content_type: "text/plain",
  size_bytes: 15,
  created_at: "2026-06-26T08:00:00Z",
};

const ART_JSON: ArtifactItem = {
  id: "art-json",
  name: "config.json",
  content_type: "application/json",
  size_bytes: 20,
  created_at: "2026-06-26T07:00:00Z",
};

// ════════════════════════════════════════════════════════════════════════════
describe("lib/artifacts — typed BFF client", () => {
  it("listArtifacts_calls_correct_endpoint", async () => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A] })),
    );
    const result = await listArtifacts();
    expect(result.data).toHaveLength(1);
    expect(result.data[0].id).toBe("art-a");
    expect(result.data[0].name).toBe("report.pdf");
    expect(result.data[0].content_type).toBe("application/pdf");
    expect(result.data[0].size_bytes).toBe(1024);
  });

  it("createArtifact_posts_correct_fields", async () => {
    let captured: { name?: string; content_type?: string; content_base64?: string } = {};
    server.use(
      http.post(URL_ARTIFACTS, async ({ request }) => {
        captured = (await request.json()) as typeof captured;
        return HttpResponse.json(
          { ...ART_A, id: "art-new" },
          { status: 201 },
        );
      }),
    );
    const result = await createArtifact("report.pdf", "application/pdf", "SGVsbG8=");
    expect(result.id).toBe("art-new");
    expect(captured.name).toBe("report.pdf");
    expect(captured.content_type).toBe("application/pdf");
    expect(captured.content_base64).toBe("SGVsbG8=");
  });

  it("downloadArtifact_returns_blob_from_raw_fetch", async () => {
    const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46]); // %PDF
    server.use(
      http.get(artUrl("art-a"), () =>
        new HttpResponse(bytes, {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        }),
      ),
    );
    const blob = await downloadArtifact("art-a");
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.size).toBe(4);
  });

  it("downloadArtifact_throws_on_non_ok", async () => {
    server.use(
      http.get(artUrl("art-missing"), () =>
        new HttpResponse(null, { status: 404 }),
      ),
    );
    await expect(downloadArtifact("art-missing")).rejects.toThrow();
  });

  it("deleteArtifact_sends_delete_to_correct_url", async () => {
    let called = false;
    server.use(
      http.delete(artUrl("art-a"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await deleteArtifact("art-a");
    expect(called).toBe(true);
  });
});

// ════════════════════════════════════════════════════════════════════════════
describe("ArtifactsWorkspace", () => {
  beforeEach(() => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [] })),
    );
  });

  // ── test_list_renders ────────────────────────────────────────────────────
  it("test_list_renders_two_artifacts", async () => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A, ART_B] })),
    );
    render(<ArtifactsWorkspace />);

    expect(await screen.findByText("report.pdf")).toBeInTheDocument();
    expect(await screen.findByText("data.csv")).toBeInTheDocument();
    // content types are shown
    expect(screen.getByText("application/pdf")).toBeInTheDocument();
    expect(screen.getByText("text/csv")).toBeInTheDocument();
    // human-readable sizes: 1024 bytes = 1.0 KB, 512 bytes = 512 B (< 1024)
    expect(screen.getByText(/1\.0\s*KB/)).toBeInTheDocument();
    expect(screen.getByText(/512\s*B/)).toBeInTheDocument();
  });

  // ── test_upload ──────────────────────────────────────────────────────────
  it("test_upload_reads_file_and_posts_to_api", async () => {
    let captured: { name?: string; content_type?: string; content_base64?: string } = {};
    const NEW_ART: ArtifactItem = {
      id: "art-new",
      name: "hello.txt",
      content_type: "text/plain",
      size_bytes: 5,
      created_at: "2026-06-26T12:00:00Z",
    };

    server.use(
      http.post(URL_ARTIFACTS, async ({ request }) => {
        captured = (await request.json()) as typeof captured;
        return HttpResponse.json(NEW_ART, { status: 201 });
      }),
    );

    vi.stubGlobal("FileReader", class {
      onload: ((ev: ProgressEvent) => void) | null = null;
      onerror: ((ev: ProgressEvent) => void) | null = null;
      readAsDataURL(_file: File) {
        setTimeout(() => {
          if (this.onload) {
            this.onload({
              target: { result: "data:text/plain;base64,aGVsbG8=" },
            } as unknown as ProgressEvent);
          }
        }, 0);
      }
    });

    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const submitBtn = screen.getByRole("button", { name: /upload/i });
    expect(submitBtn).toBeDisabled();

    const fileInput = screen.getByTestId("artifact-file-input");
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    await user.upload(fileInput, file);

    await waitFor(() => expect(submitBtn).not.toBeDisabled());

    await user.click(submitBtn);

    await waitFor(() => {
      expect(captured.name).toBe("hello.txt");
      expect(captured.content_type).toBe("text/plain");
      expect(captured.content_base64).toBe("aGVsbG8=");
    });

    await screen.findByText("hello.txt");

    vi.unstubAllGlobals();
  });

  // ── test_download ────────────────────────────────────────────────────────
  it("test_download_creates_object_url_and_clicks_anchor", async () => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A] })),
      http.get(artUrl("art-a"), () =>
        new HttpResponse(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        }),
      ),
    );

    const fakeUrl = "blob:http://localhost/fake-uuid";
    const createObjectURLSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue(fakeUrl);
    const revokeObjectURLSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    const clickSpy = vi.fn();
    const origCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = origCreate(tag);
      if (tag === "a") {
        vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(clickSpy);
      }
      return el;
    });

    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const downloadBtn = screen.getByRole("button", { name: /download report\.pdf/i });
    await user.click(downloadBtn);

    await waitFor(() => expect(createObjectURLSpy).toHaveBeenCalledWith(expect.any(Blob)));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    await waitFor(() => expect(revokeObjectURLSpy).toHaveBeenCalledWith(fakeUrl));

    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
    vi.restoreAllMocks();
  });

  // ── test_delete (guarded by confirmation dialog) ─────────────────────────
  it("test_delete_removes_item_from_list", async () => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A] })),
      http.delete(artUrl("art-a"), () => new HttpResponse(null, { status: 204 })),
    );

    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const deleteBtn = screen.getByRole("button", { name: /delete report\.pdf/i });
    await user.click(deleteBtn);

    // Confirm deletion in the guard dialog
    const dialog = screen.getByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", { name: /confirm/i });
    await user.click(confirmBtn);

    await waitFor(() =>
      expect(screen.queryByText("report.pdf")).not.toBeInTheDocument(),
    );
  });

  // ── test_list_failure_nonblocking ────────────────────────────────────────
  it("test_list_failure_nonblocking_shows_error_no_crash", async () => {
    server.use(
      http.get(URL_ARTIFACTS, () =>
        HttpResponse.json({ title: "Internal Server Error", status: 500 }, { status: 500 }),
      ),
    );
    render(<ArtifactsWorkspace />);

    const errorEl = await screen.findByRole("alert");
    expect(errorEl).toBeInTheDocument();

    expect(screen.getByTestId("artifact-file-input")).toBeInTheDocument();
  });

  // ── test_no_file_noops ────────────────────────────────────────────────────
  it("test_no_file_noops_submit_button_disabled_no_post", async () => {
    let postCalled = false;
    server.use(
      http.post(URL_ARTIFACTS, () => {
        postCalled = true;
        return HttpResponse.json(ART_A, { status: 201 });
      }),
    );

    render(<ArtifactsWorkspace />);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    const submitBtn = screen.getByRole("button", { name: /upload/i });
    expect(submitBtn).toBeDisabled();

    await userEvent.click(submitBtn);
    expect(postCalled).toBe(false);
  });
});

// ════════════════════════════════════════════════════════════════════════════
// Console-grade extensions
// ════════════════════════════════════════════════════════════════════════════

describe("ArtifactsWorkspace — search and sort", () => {
  beforeEach(() => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A, ART_B] })),
    );
  });

  it("test_search_filters_by_name", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");
    await screen.findByText("data.csv");

    const search = screen.getByRole("textbox", { name: /search/i });
    await user.type(search, "report");

    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.queryByText("data.csv")).not.toBeInTheDocument();
  });

  it("test_search_filters_by_type", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");
    await screen.findByText("data.csv");

    const search = screen.getByRole("textbox", { name: /search/i });
    await user.type(search, "text/csv");

    expect(screen.queryByText("report.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("data.csv")).toBeInTheDocument();
  });

  it("test_sort_by_name_asc", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const sortNameBtn = screen.getByRole("button", { name: /sort by name/i });
    await user.click(sortNameBtn);

    const list = screen.getByRole("list", { name: /artifact/i });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("data.csv");
    expect(items[1]).toHaveTextContent("report.pdf");
  });

  it("test_sort_by_size_asc", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const sortSizeBtn = screen.getByRole("button", { name: /sort by size/i });
    await user.click(sortSizeBtn);

    const list = screen.getByRole("list", { name: /artifact/i });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("data.csv");
    expect(items[1]).toHaveTextContent("report.pdf");
  });

  it("test_sort_by_date_asc", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const sortDateBtn = screen.getByRole("button", { name: /sort by date/i });
    await user.click(sortDateBtn);

    const list = screen.getByRole("list", { name: /artifact/i });
    const items = within(list).getAllByRole("listitem");
    // ART_B: 09:00 < ART_A: 10:00 → ascending puts ART_B first
    expect(items[0]).toHaveTextContent("data.csv");
    expect(items[1]).toHaveTextContent("report.pdf");
  });
});

describe("ArtifactsWorkspace — detail pane", () => {
  it("test_click_artifact_shows_detail_metadata", async () => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A] })),
    );
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const viewBtn = screen.getByRole("button", { name: /view report\.pdf/i });
    await user.click(viewBtn);

    const detail = await screen.findByRole("region", { name: /detail/i });
    expect(within(detail).getByText("report.pdf")).toBeInTheDocument();
    expect(within(detail).getByText("application/pdf")).toBeInTheDocument();
  });

  it("test_image_preview_shows_img_with_object_url", async () => {
    const fakeUrl = "blob:http://localhost/img-preview";
    const createObjectURLSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue(fakeUrl);
    const revokeObjectURLSpy = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => {});

    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_IMG] })),
      http.get(artUrl("art-img"), () =>
        new HttpResponse(new Uint8Array([137, 80, 78, 71]), {
          status: 200,
          headers: { "Content-Type": "image/png" },
        }),
      ),
    );

    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("photo.png");

    const viewBtn = screen.getByRole("button", { name: /view photo\.png/i });
    await user.click(viewBtn);

    const img = await screen.findByRole("img", { name: /preview of photo\.png/i });
    expect(img).toHaveAttribute("src", fakeUrl);

    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
  });

  it("test_text_preview_shows_decoded_content", async () => {
    const textContent = "Hello, preview!";
    const bytes = new TextEncoder().encode(textContent);

    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_TXT] })),
      http.get(artUrl("art-txt"), () =>
        new HttpResponse(bytes, {
          status: 200,
          headers: { "Content-Type": "text/plain" },
        }),
      ),
    );

    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("notes.txt");

    const viewBtn = screen.getByRole("button", { name: /view notes\.txt/i });
    await user.click(viewBtn);

    // Safely rendered as escaped text content, NOT dangerouslySetInnerHTML
    const previewEl = await screen.findByTestId("preview-text");
    expect(previewEl).toHaveTextContent(textContent);
  });

  it("test_json_preview_shows_formatted_json", async () => {
    const jsonData = '{"key":"value"}';
    const bytes = new TextEncoder().encode(jsonData);

    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_JSON] })),
      http.get(artUrl("art-json"), () =>
        new HttpResponse(bytes, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("config.json");

    const viewBtn = screen.getByRole("button", { name: /view config\.json/i });
    await user.click(viewBtn);

    const previewEl = await screen.findByTestId("preview-text");
    expect(previewEl).toHaveTextContent('"key"');
    expect(previewEl).toHaveTextContent('"value"');
  });
});

describe("ArtifactsWorkspace — drag and drop", () => {
  it("test_drag_drop_uploads_file", async () => {
    let captured: { name?: string; content_type?: string } = {};
    const NEW_ART: ArtifactItem = {
      id: "art-drop",
      name: "dropped.txt",
      content_type: "text/plain",
      size_bytes: 7,
      created_at: "2026-06-26T13:00:00Z",
    };

    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [] })),
      http.post(URL_ARTIFACTS, async ({ request }) => {
        captured = (await request.json()) as typeof captured;
        return HttpResponse.json(NEW_ART, { status: 201 });
      }),
    );

    vi.stubGlobal(
      "FileReader",
      class {
        onload: ((ev: ProgressEvent) => void) | null = null;
        onerror: ((ev: ProgressEvent) => void) | null = null;
        readAsDataURL(_file: File) {
          setTimeout(() => {
            if (this.onload) {
              this.onload({
                target: { result: "data:text/plain;base64,ZHJvcHBlZA==" },
              } as unknown as ProgressEvent);
            }
          }, 0);
        }
      },
    );

    render(<ArtifactsWorkspace />);
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    const dropzone = screen.getByTestId("artifact-dropzone");
    const file = new File(["dropped"], "dropped.txt", { type: "text/plain" });

    fireEvent.dragOver(dropzone, {
      dataTransfer: { types: ["Files"], files: [file] },
    });
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [file] },
    });

    await waitFor(() => expect(captured.name).toBe("dropped.txt"));
    await screen.findByText("dropped.txt");

    vi.unstubAllGlobals();
  });
});

describe("ArtifactsWorkspace — delete confirmation guard", () => {
  beforeEach(() => {
    server.use(
      http.get(URL_ARTIFACTS, () => HttpResponse.json({ data: [ART_A] })),
    );
  });

  it("test_delete_guard_shows_confirmation_dialog", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const deleteBtn = screen.getByRole("button", { name: /delete report\.pdf/i });
    await user.click(deleteBtn);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /confirm/i })).toBeInTheDocument();
    // Item still in DOM — not deleted yet
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
  });

  it("test_delete_guard_cancel_preserves_item", async () => {
    const user = userEvent.setup();
    render(<ArtifactsWorkspace />);

    await screen.findByText("report.pdf");

    const deleteBtn = screen.getByRole("button", { name: /delete report\.pdf/i });
    await user.click(deleteBtn);

    const dialog = screen.getByRole("dialog");
    const cancelBtn = within(dialog).getByRole("button", { name: /cancel/i });
    await user.click(cancelBtn);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
  });
});
