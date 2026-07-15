"use client";

/**
 * AcceptInviteForm — the public (unauthenticated) invite-accept form, rendered by
 * app/(auth)/invite/[token]/page.tsx.
 *
 * Behavior (member-invite-ui, mirrors SignupForm/LoginForm's contract shape):
 *   1. On mount, GET /api/auth/invite/{token} (public preview BFF route) and render
 *      {tenant_name, email, role}. A 404/409/410 preview response renders a
 *      SPECIFIC, friendly message (invalid link / already used / expired) — never
 *      the raw gateway title.
 *   2. Client-side Zod validation (password >=10 chars, same policy as SignupForm)
 *      before any fetch.
 *   3. POST /api/auth/invite/{token} {password}. The BFF has already accepted +
 *      auto-logged-in + set the httpOnly session cookie by the time this resolves —
 *      no localStorage write, no token ever touches this component. 201 ->
 *      router.push("/app").
 *   4. A non-2xx accept response surfaces its friendly message inline; no navigation.
 */

import { useState, useEffect, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { Button, Card, CardContent, Input, Loading, ErrorState } from "@/components/ui";

interface InvitePreview {
  tenant_name: string;
  email: string;
  role: string;
  expires_at: string;
}

const PasswordSchema = z.object({
  password: z.string().min(10, "Must be at least 10 characters"),
});

// Specific, non-500 messages for the invite lifecycle errors the gateway defines
// (404 ERR_INVITE_NOT_FOUND / 409 ERR_INVITE_NOT_PENDING / 410 ERR_INVITE_EXPIRED).
// Any other/unknown code falls back to the upstream's own title.
const INVITE_ERROR_MESSAGES: Record<string, string> = {
  ERR_INVITE_NOT_FOUND: "This invite link is invalid.",
  ERR_INVITE_NOT_PENDING: "This invite has already been used.",
  ERR_INVITE_EXPIRED: "This invite has expired. Ask your admin to send a new one.",
};

function friendlyMessage(code: string | undefined, fallbackTitle: string): string {
  if (code && INVITE_ERROR_MESSAGES[code]) return INVITE_ERROR_MESSAGES[code];
  return fallbackTitle;
}

interface AcceptInviteFormProps {
  token: string;
}

export function AcceptInviteForm({ token }: AcceptInviteFormProps) {
  const router = useRouter();

  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(true);

  const [password, setPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadPreview() {
      try {
        const res = await fetch(`/api/auth/invite/${encodeURIComponent(token)}`, {
          credentials: "include",
        });
        const body = (await res.json().catch(() => ({}))) as Partial<InvitePreview> & {
          title?: string;
          code?: string;
        };
        if (cancelled) return;

        if (!res.ok) {
          setPreviewError(friendlyMessage(body.code, body.title ?? "This invite could not be loaded."));
          return;
        }

        setPreview({
          tenant_name: body.tenant_name ?? "",
          email: body.email ?? "",
          role: body.role ?? "",
          expires_at: body.expires_at ?? "",
        });
      } catch {
        if (!cancelled) setPreviewError("This invite could not be loaded.");
      } finally {
        if (!cancelled) setIsLoadingPreview(false);
      }
    }

    void loadPreview();
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setPasswordError(null);
    setGlobalError(null);

    const result = PasswordSchema.safeParse({ password });
    if (!result.success) {
      setPasswordError(result.error.issues[0]?.message ?? "Invalid password");
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await fetch(`/api/auth/invite/${encodeURIComponent(token)}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });

      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { title?: string; code?: string };
        setGlobalError(friendlyMessage(body.code, body.title ?? "Could not accept this invite."));
        return;
      }

      // No localStorage write — the BFF already set the session cookie server-side.
      router.push("/app");
    } catch {
      setGlobalError("An unexpected error occurred");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isLoadingPreview) {
    return <Loading label="Loading invite…" />;
  }

  if (previewError || !preview) {
    return <ErrorState title={previewError ?? "This invite could not be loaded."} />;
  }

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Accept invite">
      <Card data-slot="auth-card">
        <CardContent className="flex flex-col gap-4 p-6">
          <p className="text-sm text-muted-foreground">
            You&apos;ve been invited to join{" "}
            <strong className="text-foreground">{preview.tenant_name}</strong> as{" "}
            <span className="capitalize">{preview.role}</span>.
          </p>

          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">Email</span>
            <p className="text-sm text-foreground">{preview.email}</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="invite_password" className="text-sm font-medium text-foreground">
              Set a password
            </label>
            <Input
              id="invite_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            {passwordError && (
              <p role="alert" aria-live="polite" className="text-sm text-destructive">
                {passwordError}
              </p>
            )}
          </div>

          {globalError && (
            <p role="alert" aria-live="polite" className="text-sm text-destructive">
              {globalError}
            </p>
          )}

          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Joining…" : "Accept invite"}
          </Button>
        </CardContent>
      </Card>
    </form>
  );
}
