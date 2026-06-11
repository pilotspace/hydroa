import { LoginForm } from "@/components/auth/LoginForm";

export const metadata = { title: "Hydroa" };

export default function LoginPage() {
  return (
    <main>
      <h1>Sign in to your account</h1>
      <LoginForm />
    </main>
  );
}
