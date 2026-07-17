"use client";

/**
 * SignupForm — tenant signup form
 *
 * Behavior (per frozen contract §3 v2, extended by activation-quickstart §3 v1
 * — the prior 5 behaviors below stay byte-identical; account_type is additive):
 *   1. Client-side Zod validation before any fetch
 *   2. POST /api/auth/signup BFF endpoint with credentials:"include"
 *   3. 201 → router.push("/app/keys"); no localStorage write
 *   4. 409 → inline email field error "An account with this email already exists"
 *   5. Other errors → surface problem+json title
 *   6. account_type ("personal" | "business", "personal" pre-selected) always
 *      sent in the POST body (activation-quickstart M1) — personal is Tin's
 *      locked pricing-model signup default (2026-07-16).
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
  account_type: z.enum(["personal", "business"]),
});

type AccountType = "personal" | "business";

type FieldErrors = Partial<Record<"tenant_name" | "email" | "password", string>>;

export function SignupForm() {
  const router = useRouter();
  const [tenantName, setTenantName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [accountType, setAccountType] = useState<AccountType>("personal");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setGlobalError(null);

    // Client-side validation
    const result = SignupSchema.safeParse({
      tenant_name: tenantName,
      email,
      password,
      account_type: accountType,
    });
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
        body: JSON.stringify({
          tenant_name: tenantName,
          email,
          password,
          account_type: accountType,
        }),
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
          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-sm font-medium text-foreground">Account type</legend>
            <div className="flex gap-4">
              <label
                htmlFor="account_type_personal"
                className="flex min-h-11 items-center gap-2 text-sm text-foreground"
              >
                <input
                  id="account_type_personal"
                  type="radio"
                  name="account_type"
                  value="personal"
                  checked={accountType === "personal"}
                  onChange={() => setAccountType("personal")}
                  className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                />
                Personal
              </label>
              <label
                htmlFor="account_type_business"
                className="flex min-h-11 items-center gap-2 text-sm text-foreground"
              >
                <input
                  id="account_type_business"
                  type="radio"
                  name="account_type"
                  value="business"
                  checked={accountType === "business"}
                  onChange={() => setAccountType("business")}
                  className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                />
                Business
              </label>
            </div>
          </fieldset>

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
