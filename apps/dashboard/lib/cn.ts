import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * The single class-name merger for the design system: clsx for conditional
 * composition + tailwind-merge to resolve conflicting Tailwind utilities so the
 * last-wins intent holds. Every components/ui primitive composes classes through
 * this — never string concatenation.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
