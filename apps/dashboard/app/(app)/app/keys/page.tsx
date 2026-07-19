import { KeysPage } from "@/components/keys/KeysPage";
import { JoinedWorkspaceCallout } from "@/components/onboarding/JoinedWorkspaceCallout";

export const metadata = { title: "Hydroa" };

export default function KeysRoute() {
  // /app/keys is the post-login landing (DEFAULT_POST_LOGIN) — the read-only
  // joined-workspace confirmation mounts here (domain-claims-console M6). It
  // renders null (zero IO) unless the advisory ?joined=1 signal is present.
  return (
    <>
      <JoinedWorkspaceCallout />
      <KeysPage />
    </>
  );
}
