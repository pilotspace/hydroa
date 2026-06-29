/**
 * lib/chat/tool-defs.ts — OpenAI function/tool definitions for the chat playground.
 *
 * chat-tools-functions. An operator authors tools as raw JSON (one function schema
 * per editor). We validate at the SHAPE level only — parses as JSON, a non-empty
 * string `name`, and an object `parameters` — NOT a full JSON-Schema validator: an
 * over-permissive schema reaches the model and is rejected honestly upstream. An
 * invalid entry is excluded from the wire, never sent malformed.
 */

export type ToolChoice = "auto" | "required" | "none";

/** A validated OpenAI function definition. */
export interface ToolDef {
  name: string;
  description?: string;
  parameters: Record<string, unknown>;
}

/** A tool a model asked to call, assembled from the stream (arguments = raw JSON text). */
export interface ToolCall {
  id: string;
  name: string;
  arguments: string;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Parse one editor's JSON into a ToolDef, or null when it is not a valid function
 * shape. Accepts either a bare function `{name, description?, parameters}` or the
 * wrapped `{type:"function", function:{…}}` form (so a pasted OpenAI tool works).
 */
export function parseToolDef(json: string): ToolDef | null {
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch {
    return null;
  }
  // unwrap the {type:"function", function:{…}} envelope if present
  const fn = isObject(raw) && isObject(raw.function) ? raw.function : raw;
  if (!isObject(fn)) return null;
  if (typeof fn.name !== "string" || fn.name.trim() === "") return null;
  if (!isObject(fn.parameters)) return null;
  const def: ToolDef = { name: fn.name, parameters: fn.parameters };
  if (typeof fn.description === "string" && fn.description !== "") def.description = fn.description;
  return def;
}

/** Map validated defs to the OpenAI wire `tools` array. */
export function toWireTools(defs: ToolDef[]): Array<{
  type: "function";
  function: { name: string; description?: string; parameters: Record<string, unknown> };
}> {
  return defs.map((d) => ({
    type: "function",
    function: {
      name: d.name,
      ...(d.description ? { description: d.description } : {}),
      parameters: d.parameters,
    },
  }));
}
