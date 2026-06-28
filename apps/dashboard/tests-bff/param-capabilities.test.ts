/**
 * tests-bff/param-capabilities.test.ts — RED unit matrix for the provider
 * capability map (chat-parameters-panel, v2 gating change-request).
 *
 * Keyed by the model-id provider prefix, the map mirrors how THIS gateway
 * actually translates per provider: OpenAI/OpenRouter passthrough everything;
 * Anthropic/Gemini drop frequency_penalty/presence_penalty/seed; Bedrock also
 * drops response_format. Unknown prefixes default to fully supported.
 */

import { describe, it, expect } from "vitest";
import { providerOf, isSupported, providerLabel, type CapKey } from "@/lib/chat/param-capabilities";

const ALL: CapKey[] = ["topP", "maxTokens", "frequencyPenalty", "presencePenalty", "seed", "stop", "responseFormat"];

describe("param-capabilities", () => {
  it("providerOf parses the model-id prefix (lowercased)", () => {
    expect(providerOf("openai/gpt-4o")).toBe("openai");
    expect(providerOf("Anthropic/Claude-3.5-Sonnet")).toBe("anthropic");
    expect(providerOf("bare-model")).toBe("bare-model");
  });

  it("openai / openrouter / unknown support every param", () => {
    for (const model of ["openai/gpt-4o", "openrouter/auto", "mystery/x"]) {
      for (const k of ALL) expect(isSupported(model, k)).toBe(true);
    }
  });

  it("anthropic and google drop penalties + seed, keep the rest", () => {
    for (const model of ["anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash"]) {
      expect(isSupported(model, "frequencyPenalty")).toBe(false);
      expect(isSupported(model, "presencePenalty")).toBe(false);
      expect(isSupported(model, "seed")).toBe(false);
      // still honored
      expect(isSupported(model, "topP")).toBe(true);
      expect(isSupported(model, "maxTokens")).toBe(true);
      expect(isSupported(model, "stop")).toBe(true);
      expect(isSupported(model, "responseFormat")).toBe(true);
    }
  });

  it("bedrock additionally drops response_format", () => {
    expect(isSupported("bedrock/anthropic.claude-3", "responseFormat")).toBe(false);
    expect(isSupported("bedrock/anthropic.claude-3", "seed")).toBe(false);
    expect(isSupported("bedrock/anthropic.claude-3", "topP")).toBe(true);
  });

  it("providerLabel gives a friendly name", () => {
    expect(providerLabel("anthropic/claude-3.5-sonnet")).toBe("Anthropic");
    expect(providerLabel("google/gemini-2.0-flash")).toBe("Gemini");
    expect(providerLabel("bedrock/anthropic.claude-3")).toBe("Bedrock");
    expect(providerLabel("openai/gpt-4o")).toBe("OpenAI");
  });
});
