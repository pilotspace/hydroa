"use client";

/**
 * SignupForm — tenant signup form
 *
 * Behavior (per frozen contract §3 v2):
 *   1. Client-side Zod validation before any fetch
 *   2. POST /api/auth/signup BFF endpoint with credentials:"include"
 *   3. 201 → router.push("/app/keys"); no localStorage write
 *   4. 409 → inline email field error "An account with this email already exists"
 *   5. Other errors → surface problem+json title
 */

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { BffError } from "@/lib/bff-client";
import { Button, Card, CardContent, Input } from "@/components/ui";

const SignupSchema = z.object({
  tenant_name: z.string().min(1, "Tenant name is required").max(120, "Tenant name must be at most 120 characters"),
  email: z.string().email("Invalid email address"),
  password: z.string().min(10, "Must be at least 10 characters"),
});

type FieldErrors = Partial<Record<"tenant_name" | "email" | "password", string>>;

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
      const res = await fetch("/api/auth/signup", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant_name: tenantName, email, password }),
      });

      if (!res.ok) {
        let problem: { title?: string; status?: number };
        try {
          problem = await res.json() as { title?: string; status?: number };
        } catch {
          problem = { title: "An error occurred", status: res.status };
        }
        throw new BffError(res.status, {
          title: problem.title ?? "An error occurred",
          status: res.status,
        });
      }

      // No localStorage write — cookie is set server-side by the BFF
      router.push("/app/keys");
    } catch (err) {
      if (err instanceof BffError) {
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
      <Card data-slot="auth-card">
        <CardContent className="flex flex-col gap-4 p-6">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="tenant_name" className="text-sm font-medium text-foreground">
              Tenant Name
            </label>
            <Input
              id="tenant_name"
              type="text"
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              autoComplete="organization"
            />
            {fieldErrors.tenant_name && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {fieldErrors.tenant_name}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="signup_email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <Input
              id="signup_email"
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
            <label htmlFor="signup_password" className="text-sm font-medium text-foreground">
              Password
            </label>
            <Input
              id="signup_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
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
            {isSubmitting ? "Signing up…" : "Sign up"}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}
