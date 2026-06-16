"use client";

/**
 * LoginForm — tenant login form
 *
 * Behavior (per frozen contract §3 v2):
 *   1. Client-side Zod validation before fetch
 *   2. POST /api/auth/login BFF endpoint with credentials:"include"
 *   3. 200 → router.push("/keys"); no localStorage write
 *   4. 401/error → inline error with problem+json title, no navigation
 */

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { ApiError } from "@/lib/api-client";
import { Button, Card, CardContent, Input } from "@/components/ui";

const LoginSchema = z.object({
  email: z.string().email("Invalid email address"),
  password: z.string().min(1, "Password is required"),
});

type FieldErrors = Partial<Record<"email" | "password", string>>;

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setGlobalError(null);

    // Client-side validation
    const result = LoginSchema.safeParse({ email, password });
    if (!result.success) {
      const errors: FieldErrors = {};
      for (const issue of result.error.issues) {
        const field = issue.path[0] as keyof FieldErrors;
        if (!errors[field]) errors[field] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        let problem: { title?: string };
        try {
          problem = await res.json() as { title?: string };
        } catch {
          problem = { title: "An error occurred" };
        }
        throw new ApiError(res.status, {
          title: problem.title ?? "An error occurred",
          status: res.status,
        });
      }

      // No localStorage write — cookie is set server-side by the BFF
      router.push("/keys");
    } catch (err) {
      if (err instanceof ApiError) {
        setGlobalError(err.problem.title ?? "An error occurred");
      } else {
        setGlobalError("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Log in">
      <Card data-slot="auth-card">
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="login_email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <Input
              id="login_email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
            {fieldErrors.email && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.email}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="login_password" className="text-sm font-medium text-foreground">
              Password
            </label>
            <Input
              id="login_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            {fieldErrors.password && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.password}
              </p>
            )}
          </div>

          {globalError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          )}

          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Signing in…" : "Log in"}
          </Button>

          {/* SSO login — a full-page NAVIGATION to the pre-auth BFF relay (NOT a
              fetch): the browser must follow the relay's 302 chain to the external
              IdP, which a fetch cannot do. Styled via Button asChild — stays an <a>. */}
          <Button asChild variant="outline" className="w-full">
            <a href="/api/auth/oidc/login">Sign in with SSO</a>
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}
