/**
 * tests-bff/artifacts-workspace.test.tsx — RED suite for v45 artifacts workspace.
 *
 * Pins the frozen contract:
 *   - ArtifactsWorkspace (new component): list / upload / download / delete
 *   - lib/artifacts (new BFF client): listArtifacts / createArtifact /
 *     downloadArtifact / deleteArtifact
 *
 * Mirrors memory-workspace.test.tsx for MSW setup.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
    // Default: return empty list so most tests start clean
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

    // Stub FileReader so we control the async data URL result
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

    // Wait for initial load
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

    // Upload button is disabled before a file is chosen
    const submitBtn = screen.getByRole("button", { name: /upload/i });
    expect(submitBtn).toBeDisabled();

    // Choose a file
    const fileInput = screen.getByTestId("artifact-file-input");
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    await user.upload(fileInput, file);

    // Upload button becomes enabled
    await waitFor(() => expect(submitBtn).not.toBeDisabled());

    // Submit
    await user.click(submitBtn);

    // POST was made with correct fields
    await waitFor(() => {
      expect(captured.name).toBe("hello.txt");
      expect(captured.content_type).toBe("text/plain");
      expect(captured.content_base64).toBe("aGVsbG8=");
    });

    // New item appears in the list (prepended)
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

    // Spy on URL static methods WITHOUT replacing the URL constructor
    const fakeUrl = "blob:http://localhost/fake-uuid";
    const createObjectURLSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue(fakeUrl);
    const revokeObjectURLSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    // Stub anchor click to avoid jsdom navigation errors
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

    // Wait for list to render
    await screen.findByText("report.pdf");

    // Click the Download button for ART_A
    const downloadBtn = screen.getByRole("button", { name: /download report\.pdf/i });
    await user.click(downloadBtn);

    // createObjectURL called with a Blob
    await waitFor(() => expect(createObjectURLSpy).toHaveBeenCalledWith(expect.any(Blob)));

    // The click was triggered (anchor was used)
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());

    // revokeObjectURL cleans up
    await waitFor(() => expect(revokeObjectURLSpy).toHaveBeenCalledWith(fakeUrl));

    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
    vi.restoreAllMocks();
  });

  // ── test_delete ──────────────────────────────────────────────────────────
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

    // Error state renders (non-blocking — component is still mounted)
    const errorEl = await screen.findByRole("alert");
    expect(errorEl).toBeInTheDocument();

    // The workspace UI is still accessible — file input + upload button still present
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

    // Clicking a disabled button does nothing
    await userEvent.click(submitBtn);
    expect(postCalled).toBe(false);
  });
});
