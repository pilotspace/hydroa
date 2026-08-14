/**
 * components/evals/errors.ts — evals-console TASK.md §3 CONTRACT M6 /
 * R:NULL_RENDER_LEAK: every error state names the FAILED SUBSYSTEM + a next step, never
 * the user's data. Shared by every evals page so the wording stays consistent.
 */

import { BffError } from "@/lib/bff-client";

export function getEvalsErrorTitle(err: unknown, subsystem: string): string {
  if (err instanceof BffError && err.status === 403) return `You don't have access to ${subsystem}`;
  if (err instanceof BffError && err.status === 404) return `${subsystem} not found`;
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return `Failed to load ${subsystem}`;
}
