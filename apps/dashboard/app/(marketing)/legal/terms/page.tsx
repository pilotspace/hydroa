import { LegalPage, LegalSection } from "@/components/marketing/legal-page";
import { buildMetadata } from "@/lib/seo";

export const metadata = buildMetadata({
  title: "Terms of Service",
  description:
    "The terms governing your use of the Hydroa AI proxy platform.",
  path: "/legal/terms",
});

export default function TermsPage() {
  return (
    <LegalPage title="Terms of Service" lastUpdated="June 24, 2026">
      <LegalSection id="acceptance" heading="1. Acceptance of terms">
        <p>
          By accessing or using Hydroa you agree to these terms. If you are using
          the service on behalf of an organisation, you represent that you have
          authority to bind that organisation.
        </p>
      </LegalSection>
      <LegalSection id="use" heading="2. Use of the service">
        <p>
          You are responsible for the activity under your tenant and API keys, and
          for complying with the usage policies of the upstream AI providers you
          route through Hydroa.
        </p>
      </LegalSection>
      <LegalSection id="billing" heading="3. Billing">
        <p>
          Usage is metered per tenant. Charges reflect proxied requests and the
          plan tier in effect; provider costs are passed through and reconciled.
        </p>
      </LegalSection>
      <LegalSection id="liability" heading="4. Limitation of liability">
        <p>
          The service is provided &ldquo;as is.&rdquo; To the maximum extent
          permitted by law, Hydroa is not liable for indirect or consequential
          damages arising from use of the service.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
