import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Terms of Service — MetricsPro',
  description: 'Terms of Service for the MetricsPro application, developed by IT Solutions of LI Inc.',
}

const UPDATED = 'September 4, 2026'

export default function TermsOfServicePage() {
  const h2: React.CSSProperties = { fontSize: 19, fontWeight: 700, margin: '28px 0 8px', color: '#0f172a' }
  const p: React.CSSProperties = { margin: '0 0 12px', lineHeight: 1.6 }
  const li: React.CSSProperties = { margin: '0 0 6px', lineHeight: 1.55 }
  return (
    <main style={{ maxWidth: 820, margin: '0 auto', padding: '48px 24px 80px', color: '#1f2937',
      fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif', fontSize: 15 }}>
      <h1 style={{ fontSize: 30, fontWeight: 800, margin: '0 0 4px', color: '#0f172a' }}>Terms of Service</h1>
      <p style={{ color: '#64748b', margin: '0 0 24px', fontSize: 14 }}>Last updated: {UPDATED}</p>

      <p style={p}>
        These Terms of Service (&ldquo;Terms&rdquo;) govern access to and use of the <strong>MetricsPro</strong>{' '}
        application (the &ldquo;Service&rdquo;), developed and operated by <strong>IT Solutions of LI Inc</strong>{' '}
        (&ldquo;we&rdquo;, &ldquo;us&rdquo;, or &ldquo;our&rdquo;). MetricsPro is a private, business-to-business
        operations and commission-analytics platform provided to authorized business customers (each a
        &ldquo;Customer&rdquo;) and their authorized staff. By accessing or using the Service you agree to these Terms
        on behalf of yourself and, where applicable, the Customer organization that authorized your access.
      </p>

      <h2 style={h2}>1. Access and Accounts</h2>
      <p style={p}>
        The Service is not a consumer product and is available only to users authorized by a Customer. You are
        responsible for maintaining the confidentiality of your login credentials and for all activity under your
        account. Access levels and permissions within the Service are assigned by the Customer&rsquo;s administrators,
        and we may suspend access that we reasonably believe is unauthorized or poses a security risk.
      </p>

      <h2 style={h2}>2. Customer Data</h2>
      <p style={p}>
        The Customer retains all rights to the business data it or its staff submit to or generate within the Service
        (&ldquo;Customer Data&rdquo;), including sales, commission, inventory, accounting, staffing, and document data.
        We process Customer Data solely to provide, maintain, secure, and improve the Service for that Customer, as
        further described in our Privacy Policy. Each Customer&rsquo;s data is logically isolated from other
        customers&rsquo; data.
      </p>

      <h2 style={h2}>3. Acceptable Use</h2>
      <p style={p}>You agree not to:</p>
      <ul style={{ margin: '0 0 12px', paddingLeft: 22 }}>
        <li style={li}>access or attempt to access data belonging to another customer or another user beyond your assigned permissions;</li>
        <li style={li}>interfere with or disrupt the integrity, performance, or security of the Service;</li>
        <li style={li}>reverse engineer, copy, resell, or sublicense the Service except as permitted in a written agreement with us;</li>
        <li style={li}>use the Service in violation of applicable law, including wage, tax, and data-protection law applicable to the Customer&rsquo;s business.</li>
      </ul>

      <h2 style={h2}>4. Third-Party Integrations</h2>
      <p style={p}>
        The Service can connect to third-party services (for example email providers, storage providers, carrier and
        point-of-sale reporting portals) at the Customer&rsquo;s direction to import and reconcile the Customer&rsquo;s
        own business data. Use of those services is governed by their own terms, and the Customer is responsible for
        having the right to connect them. Integration credentials are used only to perform the connected function.
      </p>

      <h2 style={h2}>5. Service Availability and Changes</h2>
      <p style={p}>
        We work to keep the Service available and accurate, but the Service is provided on an &ldquo;as is&rdquo; and
        &ldquo;as available&rdquo; basis. We may modify, add, or remove features, and may perform maintenance that
        temporarily limits availability. Reports and calculations in the Service assist the Customer&rsquo;s business
        decisions; the Customer remains responsible for verifying figures used for payroll, tax, and financial filings.
      </p>

      <h2 style={h2}>6. Disclaimers and Limitation of Liability</h2>
      <p style={p}>
        To the maximum extent permitted by law, we disclaim all warranties, express or implied, including
        merchantability, fitness for a particular purpose, and non-infringement. To the maximum extent permitted by
        law, our aggregate liability arising out of or relating to the Service is limited to the amounts paid by the
        Customer for the Service in the twelve months preceding the claim, and neither party is liable for indirect,
        incidental, special, or consequential damages.
      </p>

      <h2 style={h2}>7. Termination</h2>
      <p style={p}>
        A Customer&rsquo;s administrators may deactivate user access at any time. We may suspend or terminate access
        for material breach of these Terms or where required to protect the Service or its users. Upon termination of
        a Customer&rsquo;s subscription, we will make the Customer&rsquo;s data available for export for a reasonable
        period as agreed with the Customer.
      </p>

      <h2 style={h2}>8. Changes to These Terms</h2>
      <p style={p}>
        We may update these Terms from time to time. The &ldquo;Last updated&rdquo; date above reflects the latest
        revision. Continued use of the Service after an update constitutes acceptance of the revised Terms.
      </p>

      <h2 style={h2}>9. Contact</h2>
      <p style={p}>
        Questions about these Terms can be directed to <strong>IT Solutions of LI Inc</strong> at{' '}
        <a href="mailto:sanjot@cellfonzrus.com" style={{ color: '#2563eb' }}>sanjot@cellfonzrus.com</a>.
      </p>
    </main>
  )
}
