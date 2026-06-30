/**
 * lib/voice-cost.ts — per-turn chat cost helper for the voice playground.
 *
 * Mirrors the formula used by ChatWorkspace:
 *   cost = promptTokens * price.p + completionTokens * price.c
 *
 * Returns undefined (honest absent) when the model has no known price in the
 * price map — never returns 0 as a fabricated cost. STT and TTS token counts
 * are provider-billed and not available client-side; only the chat turn tokens
 * are priced here. The UI shows "—" when cost is undefined.
 *
 * The price map is populated from GET /admin/catalog/models
 * (ModelEntry.prompt_per_token / completion_per_token fields).
 */

/** A per-token price entry from the model catalog. */
export interface VoicePriceEntry {
  /** Cost per prompt token in USD. */
  p: number;
  /** Cost per completion token in USD. */
  c: number;
}

/**
 * Compute the USD cost for a chat completion turn.
 *
 * @param model - The model id used for the chat completion (e.g. "openai/gpt-4o")
 * @param promptTokens - Number of prompt tokens consumed
 * @param completionTokens - Number of completion tokens generated
 * @param priceMap - Map from model id to per-token prices
 * @returns Cost in USD, or undefined if the model is not priced
 */
export function computeTurnCost(
  model: string,
  promptTokens: number,
  completionTokens: number,
  priceMap: Map<string, VoicePriceEntry>,
): number | undefined {
  const price = priceMap.get(model);
  if (!price) return undefined;
  const cost = promptTokens * price.p + completionTokens * price.c;
  return Number.isFinite(cost) ? cost : undefined;
}
