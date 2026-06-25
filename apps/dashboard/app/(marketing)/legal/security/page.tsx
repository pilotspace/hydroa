import { LegalPage, LegalSection } from "@/components/marketing/legal-page";

export const metadata = { title: "Security — Hydroa" };

export default function SecurityPage() {
  return (
    <LegalPage title="Security" lastUpdated="June 24, 2026">
      <LegalSection id="tenant-isolation" heading="1. Tenant isolation">
        <p>
          Every tenant-owned record carries a tenant identifier and every query is
          tenant-scoped, so keys, usage, and billing never cross tenant boundaries.
        </p>
      </LegalSection>
      <LegalSection id="key-handling" heading="2. Key handling">
        <p>
          API key secrets are stored only as hashes at rest and shown once at
          creation. Bring-your-own provider keys are encrypted at rest per tenant.
        </p>
      </LegalSection>
      <LegalSection id="access-control" heading="3. Access control">
        <p>
          Administrative access uses OIDC-based SSO with role-based authorization.
          The gateway validates every request; the dashboard never weakens that.
        </p>
      </LegalSection>
      <LegalSection id="disclosure" heading="4. Vulnerability disclosure">
        <p>
          Report suspected vulnerabilities to our security team. We aim to
          acknowledge reports promptly and remediate verified issues.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
