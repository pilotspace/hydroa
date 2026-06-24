import { LegalPage, LegalSection } from "@/components/marketing/legal-page";

export const metadata = { title: "Privacy Policy — Hydroa" };

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy Policy" lastUpdated="June 24, 2026">
      <LegalSection id="data-we-collect" heading="1. Data we collect">
        <p>
          We collect tenant account details, API usage metadata (model, provider,
          token counts, cost, latency), and authentication identifiers needed to
          operate the service.
        </p>
      </LegalSection>
      <LegalSection id="how-we-use" heading="2. How we use data">
        <p>
          Usage metadata powers per-tenant cost tracking, billing reconciliation,
          and operational monitoring. We do not sell your data.
        </p>
      </LegalSection>
      <LegalSection id="retention" heading="3. Data retention">
        <p>
          Usage records are retained per your tenant&rsquo;s configured retention
          window. Audit and billing records may be retained longer where required
          for compliance.
        </p>
      </LegalSection>
      <LegalSection id="your-rights" heading="4. Your rights">
        <p>
          You may request access to, export of, or deletion of your tenant data
          subject to legal and contractual retention obligations.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
