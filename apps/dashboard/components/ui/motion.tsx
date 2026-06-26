import * as React from "react";
import { cn } from "@/lib/cn";

/**
 * Reveal — a progressive, reduced-motion-safe entrance (v50 motion-primitives).
 *
 * Children are rendered UNCONDITIONALLY — content never waits on (or hides
 * behind) an animation. A subtle fade/slide-up entrance is applied ONLY under
 * Tailwind's `motion-safe:` variant, so a user with `prefers-reduced-motion`
 * sees the content immediately with no movement. The global reduced-motion net
 * in globals.css is the unconditional backstop for all other animations.
 *
 * Motion is polish layered on top of a fully-usable static UI — never a gate.
 */
export interface RevealProps extends React.HTMLAttributes<HTMLElement> {
  /** Element to render (default "div"). */
  as?: React.ElementType;
  /** Optional motion-safe stagger delay (ms). */
  delay?: 0 | 75 | 150 | 300;
}

const DELAY_CLASS: Record<number, string> = {
  75: "motion-safe:delay-75",
  150: "motion-safe:delay-150",
  300: "motion-safe:delay-300",
};

export function Reveal({
  as: Tag = "div",
  delay = 0,
  className,
  children,
  ...rest
}: RevealProps) {
  return (
    <Tag
      className={cn(
        "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-500",
        delay ? DELAY_CLASS[delay] : undefined,
        className,
      )}
      {...rest}
    >
      {children}
    </Tag>
  );
}
