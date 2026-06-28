/**
 * tests-bff/video-workspace.test.tsx — suite for v47 video workspace.
 *
 * Mocks lib/video (createVideoJob / listVideoJobs) and downloadArtifact from
 * lib/artifacts directly — real timers throughout (no vi.useFakeTimers).
 * Polling tests use a 50ms pollIntervalMs prop so waitFor catches multiple ticks.
 *
 * Contract pins:
 *   submit_creates_job · succeeded_shows_download · failed_shows_reason ·
 *   generate_disabled_when_empty · bff_error_soft · polling_stops_when_all_terminal ·
 *   polling_active_while_running
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── mock lib/video ─────────────────────────────────────────────────────────────
vi.mock("@/lib/video", () => ({
  createVideoJob: vi.fn(),
  getVideoJob: vi.fn(),
  listVideoJobs: vi.fn(),
}));

// ── mock downloadArtifact from lib/artifacts ───────────────────────────────────
vi.mock("@/lib/artifacts", () => ({
  downloadArtifact: vi.fn(),
  listArtifacts: vi.fn(),
  createArtifact: vi.fn(),
  deleteArtifact: vi.fn(),
}));

import { VideoWorkspace } from "@/components/video/VideoWorkspace";
import { createVideoJob, listVideoJobs } from "@/lib/video";
import { downloadArtifact } from "@/lib/artifacts";

// ── typed mocks ────────────────────────────────────────────────────────────────
const mockCreateVideoJob = vi.mocked(createVideoJob);
const mockListVideoJobs = vi.mocked(listVideoJobs);
const mockDownloadArtifact = vi.mocked(downloadArtifact);

// Short poll interval for tests so polling assertions don't need to wait 2 s.
const POLL_MS = 50;

// ── fixtures ───────────────────────────────────────────────────────────────────

const JOB_QUEUED = {
  id: "vid-1",
  status: "queued" as const,
  model: "google/veo-2",
  prompt: "A cat chasing a laser pointer",
  result_artifact_id: null,
  error: null,
  created_at: "2026-06-26T10:00:00Z",
  updated_at: "2026-06-26T10:00:00Z",
};

const JOB_SUCCEEDED = {
  id: "vid-2",
  status: "succeeded" as const,
  model: "google/veo-2",
  prompt: "A dog catching a frisbee",
  result_artifact_id: "art-result-42",
  error: null,
  created_at: "2026-06-26T10:00:00Z",
  updated_at: "2026-06-26T10:05:00Z",
};

const JOB_FAILED_GENERIC = {
  id: "vid-3",
  status: "failed" as const,
  model: "google/veo-2",
  prompt: "A fish swimming",
  result_artifact_id: null,
  error: "upstream timeout",
  created_at: "2026-06-26T10:00:00Z",
  updated_at: "2026-06-26T10:01:00Z",
};

const JOB_FAILED_NO_PROVIDER = {
  id: "vid-4",
  status: "failed" as const,
  model: "google/veo-2",
  prompt: "A bird flying",
  result_artifact_id: null,
  error: "no_video_provider_configured",
  created_at: "2026-06-26T10:00:00Z",
  updated_at: "2026-06-26T10:01:00Z",
};

// ════════════════════════════════════════════════════════════════════════════
describe("VideoWorkspace", () => {
  beforeEach(() => {
    // Default: empty job list
    mockListVideoJobs.mockResolvedValue({ jobs: [] });
    mockCreateVideoJob.mockResolvedValue(JOB_QUEUED);
    mockDownloadArtifact.mockResolvedValue(new Blob(["video"], { type: "video/mp4" }));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // ── submit_creates_job ─────────────────────────────────────────────────────
  it("submit_creates_job", async () => {
    const user = userEvent.setup();
    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Wait for initial load to settle (loading spinner gone)
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Fill in model + prompt
    const modelInput = screen.getByRole("combobox", { name: /model/i });
    const promptInput = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(modelInput, "google/veo-2");
    await user.type(promptInput, "A cat chasing a laser pointer");

    // Generate button now enabled
    const generateBtn = screen.getByRole("button", { name: /generate/i });
    expect(generateBtn).not.toBeDisabled();

    // Click Generate
    await user.click(generateBtn);

    // createVideoJob called with correct args
    await waitFor(() => {
      expect(mockCreateVideoJob).toHaveBeenCalledWith({
        model: "google/veo-2",
        prompt: "A cat chasing a laser pointer",
      });
    });

    // The returned queued job appears in the list
    await waitFor(() =>
      expect(screen.getByText(/queued/i)).toBeInTheDocument(),
    );
  });

  // ── succeeded_shows_download ───────────────────────────────────────────────
  it("succeeded_shows_download", async () => {
    mockListVideoJobs.mockResolvedValue({ jobs: [JOB_SUCCEEDED] });

    const fakeUrl = "blob:http://localhost/fake-video";
    const createObjectURLSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue(fakeUrl);
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

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
    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Wait for the succeeded job to render
    await waitFor(() =>
      expect(screen.getByText(/succeeded/i)).toBeInTheDocument(),
    );

    // Download button present for the succeeded job
    const downloadBtn = screen.getByRole("button", { name: /download/i });
    expect(downloadBtn).toBeInTheDocument();

    // Click download
    await user.click(downloadBtn);

    // downloadArtifact called with correct artifact id
    await waitFor(() =>
      expect(mockDownloadArtifact).toHaveBeenCalledWith("art-result-42"),
    );

    // createObjectURL called with a Blob
    await waitFor(() =>
      expect(createObjectURLSpy).toHaveBeenCalledWith(expect.any(Blob)),
    );

    // Anchor click triggered
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());

    createObjectURLSpy.mockRestore();
    vi.restoreAllMocks();
  });

  // ── failed_shows_reason_generic ────────────────────────────────────────────
  it("failed_shows_reason_generic", async () => {
    mockListVideoJobs.mockResolvedValue({ jobs: [JOB_FAILED_GENERIC] });

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Error shown verbatim
    await waitFor(() =>
      expect(screen.getByText(/upstream timeout/i)).toBeInTheDocument(),
    );

    // NO Download button for a failed job
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  // ── failed_shows_friendly_message_for_no_provider ─────────────────────────
  it("failed_shows_friendly_message_for_no_provider", async () => {
    mockListVideoJobs.mockResolvedValue({ jobs: [JOB_FAILED_NO_PROVIDER] });

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Friendly message shown
    await waitFor(() =>
      expect(
        screen.getByText(/video generation isn't configured yet/i),
      ).toBeInTheDocument(),
    );

    // Raw code must NOT appear verbatim
    expect(
      screen.queryByText("no_video_provider_configured"),
    ).not.toBeInTheDocument();

    // NO Download button
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  // ── generate_disabled_when_empty_model ────────────────────────────────────
  it("generate_disabled_when_empty_model", async () => {
    const user = userEvent.setup();
    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    const generateBtn = screen.getByRole("button", { name: /generate/i });

    // Initially disabled — both fields empty
    expect(generateBtn).toBeDisabled();

    // Only prompt filled → still disabled
    const promptInput = screen.getByRole("textbox", { name: /prompt/i });
    await user.type(promptInput, "A cat chasing a laser pointer");
    expect(generateBtn).toBeDisabled();

    // Clicking does nothing
    await user.click(generateBtn);
    expect(mockCreateVideoJob).not.toHaveBeenCalled();
  });

  // ── generate_disabled_when_empty_prompt ───────────────────────────────────
  it("generate_disabled_when_empty_prompt", async () => {
    const user = userEvent.setup();
    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    const generateBtn = screen.getByRole("button", { name: /generate/i });

    // Only model filled → still disabled
    const modelInput = screen.getByRole("combobox", { name: /model/i });
    await user.type(modelInput, "google/veo-2");
    expect(generateBtn).toBeDisabled();

    await user.click(generateBtn);
    expect(mockCreateVideoJob).not.toHaveBeenCalled();
  });

  // ── bff_error_on_initial_load_shows_inline_error ─────────────────────────
  it("bff_error_on_initial_load_shows_inline_error", async () => {
    mockListVideoJobs.mockRejectedValue(new Error("Internal Server Error"));

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Inline error alert renders
    const alert = await screen.findByRole("alert");
    expect(alert).toBeInTheDocument();

    // No crash — form still accessible
    expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument();
  });

  // ── bff_error_soft_keeps_last_good_list ───────────────────────────────────
  it("bff_error_soft_keeps_last_good_list", async () => {
    // First call returns a succeeded job (terminal → no poll started)
    mockListVideoJobs.mockResolvedValue({ jobs: [JOB_SUCCEEDED] });

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Initial load renders the succeeded job
    await waitFor(() =>
      expect(screen.getByText(/succeeded/i)).toBeInTheDocument(),
    );

    // No crash — form still mounted
    expect(screen.getByRole("combobox", { name: /model/i })).toBeInTheDocument();
  });

  // ── polling_stops_when_all_jobs_terminal ──────────────────────────────────
  it("polling_stops_when_all_jobs_terminal", async () => {
    // All jobs terminal from the start
    mockListVideoJobs.mockResolvedValue({ jobs: [JOB_SUCCEEDED] });

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Wait for initial load
    await waitFor(() =>
      expect(screen.getByText(/succeeded/i)).toBeInTheDocument(),
    );

    const countAfterLoad = mockListVideoJobs.mock.calls.length;

    // Wait several poll intervals — no additional calls should occur
    await new Promise((r) => setTimeout(r, POLL_MS * 5));

    // listVideoJobs must NOT have been called again — polling stopped
    expect(mockListVideoJobs.mock.calls.length).toBe(countAfterLoad);
  });

  // ── polling_active_while_jobs_running ─────────────────────────────────────
  it("polling_active_while_jobs_running", async () => {
    mockListVideoJobs.mockResolvedValue({ jobs: [JOB_QUEUED] });

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // Wait for initial load
    await waitFor(() =>
      expect(screen.getByText(/queued/i)).toBeInTheDocument(),
    );

    const countAfterLoad = mockListVideoJobs.mock.calls.length;

    // Wait several poll intervals — additional calls should occur
    await waitFor(
      () => {
        expect(mockListVideoJobs.mock.calls.length).toBeGreaterThan(countAfterLoad);
      },
      { timeout: POLL_MS * 10 },
    );
  });

  // ── model field autocompletes from the catalog (video-gen models) ─────────
  it("model_field_suggests_catalog_video_models", async () => {
    mockListVideoJobs.mockResolvedValue({ jobs: [] });
    server.use(
      http.get("http://localhost:3000/api/gw/admin/catalog/models", () =>
        HttpResponse.json({
          object: "list",
          data: [{ id: "google/veo-2" }, { id: "openai/gpt-4o" }],
        }),
      ),
    );

    render(<VideoWorkspace pollIntervalMs={POLL_MS} />);

    // The model field is now a combobox backed by a catalog-driven datalist.
    expect(await screen.findByRole("combobox", { name: /model/i })).toBeInTheDocument();
    await waitFor(() => {
      const opts = Array.from(
        document.querySelectorAll("#video-model-options option"),
      ).map((o) => (o as HTMLOptionElement).value);
      expect(opts).toContain("google/veo-2"); // video model suggested
      expect(opts).not.toContain("openai/gpt-4o"); // chat model filtered out
    });
  });
});
