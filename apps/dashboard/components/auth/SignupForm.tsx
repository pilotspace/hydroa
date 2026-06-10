"use client";

/**
 * SignupForm — tenant signup form
 *
 * Behavior (per frozen contract §3):
 *   1. Client-side Zod validation before any fetch
 *   2. POST /admin/auth/signup → 201
 *   3. Auto-login: POST /admin/auth/login → store JWT → router.push("/keys")
 *   4. 409 → inline email field error "An account with this email already exists"
 *   5. Other errors → surface problem+json title
 */

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { apiPost, ApiError } from "@/lib/api-client";
import { setToken } from "@/lib/auth";

const SignupSchema = z.object({
  tenant_name: z.string().min(1, "Tenant name is required").max(120, "Tenant name must be at most 120 characters"),
  email: z.string().email("Invalid email address"),
  password: z.string().min(10, "Must be at least 10 characters"),
});

type FieldErrors = Partial<Record<"tenant_name" | "email" | "password", string>>;

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export function SignupForm() {
  const router = useRouter();
  const [tenantName, setTenantName] = useState("");
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
    const result = SignupSchema.safeParse({ tenant_name: tenantName, email, password });
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
      await apiPost("/admin/auth/signup", { tenant_name: tenantName, email, password });

      // Auto-login after successful signup
      const loginRes = await apiPost<LoginResponse>("/admin/auth/login", { email, password });
      setToken(loginRes.access_token);
      router.push("/keys");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          setFieldErrors({ email: "An account with this email already exists" });
        } else {
          setGlobalError(err.problem.title ?? "An error occurred");
        }
      } else {
        setGlobalError("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Sign up">
      <div>
        <label htmlFor="tenant_name">Tenant Name</label>
        <input
          id="tenant_name"
          type="text"
          value={tenantName}
          onChange={(e) => setTenantName(e.target.value)}
          autoComplete="organization"
        />
        {fieldErrors.tenant_name && (
          <p role="alert" aria-live="polite">{fieldErrors.tenant_name}</p>
        )}
      </div>

      <div>
        <label htmlFor="signup_email">Email</label>
        <input
          id="signup_email"
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
        <label htmlFor="signup_password">Password</label>
        <input
          id="signup_password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
        />
        {fieldErrors.password && (
          <p role="alert" aria-live="polite">{fieldErrors.password}</p>
        )}
      </div>

      {globalError && (
        <p role="alert" aria-live="polite">{globalError}</p>
      )}

      <button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Signing up…" : "Sign up"}
      </button>
    </form>
  );
}
