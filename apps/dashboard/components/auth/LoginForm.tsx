"use client";

/**
 * LoginForm — tenant login form
 *
 * Behavior (per frozen contract §3):
 *   1. Client-side Zod validation before fetch
 *   2. POST /admin/auth/login → 200 → store JWT → router.push("/keys")
 *   3. 401 → inline error with problem+json title, no navigation
 */

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { apiPost, ApiError } from "@/lib/api-client";
import { setToken } from "@/lib/auth";

const LoginSchema = z.object({
  email: z.string().email("Invalid email address"),
  password: z.string().min(1, "Password is required"),
});

type FieldErrors = Partial<Record<"email" | "password", string>>;

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

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
      const res = await apiPost<LoginResponse>("/admin/auth/login", { email, password });
      setToken(res.access_token);
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
      <div>
        <label htmlFor="login_email">Email</label>
        <input
          id="login_email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
        />
        {fieldErrors.email && (
          <p role="alert" aria-live="polite">{fieldErrors.email}</p>
        )}
      </div>

      <div>
        <label htmlFor="login_password">Password</label>
        <input
          id="login_password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        {fieldErrors.password && (
          <p role="alert" aria-live="polite">{fieldErrors.password}</p>
        )}
      </div>

      {globalError && (
        <p role="alert" aria-live="polite">{globalError}</p>
      )}

      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Signing in…" : "Log in"}
      </button>
    </form>
  );
}
