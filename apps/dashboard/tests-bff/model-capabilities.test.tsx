/**
 * tests-bff/model-capabilities.test.tsx — RED→GREEN for v55 task 2
 * "capabilities-admin-surface": Inputs column with input_modalities Badge chips
 * on the ModelsPage dashboard surface.
 *
 * RED before build: AdminModelItem has no input_modalities field and the columns
 * array has no "Inputs" column, so the badge/header assertions fail.
 *
 * Runs in the "bff" vitest project (msw same-origin handlers, server.use overrides).
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import React from "react";

import { ModelsPage } from "@/components/models/ModelsPage";
import { server } from "./mocks/server";

const APP = "http://localhost:3000";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={makeQueryClient()}>{children}</QueryClientProvider>;
}

function listResponse(models: unknown[]) {
  return { object: "list", data: models };
}

describe("ModelsPage — Inputs column (input_modalities Badge chips)", () => {
  it("test_inputs_column_renders_text_and_image_badges: shows chips for ['image','text'] and Switch intact", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(
          listResponse([
            {
              id: "openai/gpt-4o",
              name: "GPT-4o",
              context_length: 128000,
              enabled: true,
              input_modalities: ["image", "text"],
            },
          ]),
        ),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    // Wait for list to load
    await screen.findByText("GPT-4o");

    // "Inputs" column header must be present
    expect(screen.getByText("Inputs")).toBeInTheDocument();

    // Both modality Badge chips rendered
    expect(screen.getByText("text")).toBeInTheDocument();
    expect(screen.getByText("image")).toBeInTheDocument();

    // Existing enable Switch still present (regression guard — existing behavior intact)
    expect(screen.getByRole("switch", { name: /gpt-4o/i })).toBeInTheDocument();
  });

  it("test_inputs_column_single_text_badge: exactly one 'text' badge for ['text'], no image/audio", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(
          listResponse([
            {
              id: "anthropic/claude-3-5-sonnet",
              name: "Claude 3.5 Sonnet",
              context_length: 200000,
              enabled: true,
              input_modalities: ["text"],
            },
          ]),
        ),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("Claude 3.5 Sonnet");

    // Exactly one "text" Badge chip — no duplicates
    const textBadges = screen.getAllByText("text");
    expect(textBadges).toHaveLength(1);

    // No "image" or "audio" chip
    expect(screen.queryByText("image")).not.toBeInTheDocument();
    expect(screen.queryByText("audio")).not.toBeInTheDocument();

    // Switch still rendered
    expect(screen.getByRole("switch", { name: /claude 3\.5 sonnet/i })).toBeInTheDocument();
  });

  it("test_inputs_column_empty_array: no chips rendered, no crash", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(
          listResponse([
            {
              id: "prov/model-x",
              name: "Model X",
              context_length: null,
              enabled: false,
              input_modalities: [],
            },
          ]),
        ),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("Model X");

    // No modality badges rendered for empty array
    expect(screen.queryByText("text")).not.toBeInTheDocument();
    expect(screen.queryByText("image")).not.toBeInTheDocument();
    // No crash, row present
    expect(screen.getByText("Model X")).toBeInTheDocument();
  });

  it("test_inputs_column_undefined_modalities: no crash when input_modalities absent", async () => {
    server.use(
      http.get(`${APP}/api/gw/admin/models`, () =>
        HttpResponse.json(
          listResponse([
            {
              id: "prov/legacy",
              name: "Legacy Model",
              context_length: 4096,
              enabled: true,
              // No input_modalities field — tolerate undefined
            },
          ]),
        ),
      ),
    );
    render(<ModelsPage />, { wrapper: Wrapper });

    await screen.findByText("Legacy Model");

    // No crash, no phantom badges
    expect(screen.queryByText("text")).not.toBeInTheDocument();
    expect(screen.queryByText("image")).not.toBeInTheDocument();
    // Switch still rendered
    expect(screen.getByRole("switch", { name: /legacy model/i })).toBeInTheDocument();
  });
});
