/**
 * lib/chat/param-capabilities.ts — which sampling params each provider honors.
 *
 * chat-parameters-panel (v2 gating change-request). The dashboard speaks the
 * OpenAI-compatible wire, but the gateway translates per provider and silently
 * DROPS params a provider can't honor. Rather than ship a no-op key (and mislead
 * the user), the UI gates: an unsupported control is disabled + annotated and the
 * key is omitted from the body. This map mirrors the gateway's real translation
 * (traced file:line): OpenAI / OpenRouter passthrough everything; Anthropic and
 * Google(Gemini) drop frequency_penalty / presence_penalty / seed; Bedrock also
 * drops response_format. An unknown prefix defaults to fully supported (the safe
 * passthrough assumption — never gate a param we can't prove is dropped).
 *
 * Granularity is PROVIDER-level, not per-model: the finer per-model 400 edges
 * (temperature/top_p on Claude Opus 4.7+, max_tokens on OpenAI o-series) surface
 * as honest upstream errors and are tracked as a gateway-robustness follow-up.
 */

export type CapKey =
  | "topP"
  | "maxTokens"
  | "frequencyPenalty"
  | "presencePenalty"
  | "seed"
  | "stop"
  | "responseFormat";

/** The leading `provider/` segment of a model id, lowercased (or the whole id if unprefixed). */
export function providerOf(model: string): string {
  return (model.split("/")[0] ?? "").toLowerCase();
}

const PENALTIES_AND_SEED: CapKey[] = ["frequencyPenalty", "presencePenalty", "seed"];

/** Params a provider does NOT honor (anything absent ⇒ supported). */
const UNSUPPORTED: Record<string, CapKey[]> = {
  anthropic: PENALTIES_AND_SEED,
  google: PENALTIES_AND_SEED,
  gemini: PENALTIES_AND_SEED,
  bedrock: [...PENALTIES_AND_SEED, "responseFormat"],
  amazon: [...PENALTIES_AND_SEED, "responseFormat"],
};

/** True when the selected model's provider honors `key` (drives both gating and body inclusion). */
export function isSupported(model: string, key: CapKey): boolean {
  return !(UNSUPPORTED[providerOf(model)] ?? []).includes(key);
}

const LABELS: Record<string, string> = {
  openai: "OpenAI",
  openrouter: "OpenRouter",
  anthropic: "Anthropic",
  google: "Gemini",
  gemini: "Gemini",
  bedrock: "Bedrock",
  amazon: "Bedrock",
  azure: "Azure",
};

/** A human-friendly provider name for the "Ignored by …" annotation. */
export function providerLabel(model: string): string {
  const prov = providerOf(model);
  return LABELS[prov] ?? prov;
}
