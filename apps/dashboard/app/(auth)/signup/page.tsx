import { SignupForm } from "@/components/auth/SignupForm";
import { AuthShell } from "@/components/ui";

export default function SignupPage() {
  return (
    <AuthShell>
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Create your account
      </h1>
      <SignupForm />
    </AuthShell>
  );
}
