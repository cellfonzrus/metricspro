import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Privacy Policy — MetricsPro',
  description: 'Privacy Policy for the MetricsPro application, developed by IT Solutions of LI Inc.',
}

const UPDATED = 'June 19, 2026'

export default function PrivacyPolicyPage() {
  const h2: React.CSSProperties = { fontSize: 19, fontWeight: 700, margin: '28px 0 8px', color: '#0f172a' }
  const p: React.CSSProperties = { margin: '0 0 12px', lineHeight: 1.6 }
  const li: React.CSSProperties = { margin: '0 0 6px', lineHeight: 1.55 }
  return (
    <main style={{ maxWidth: 820, margin: '0 auto', padding: '48px 24px 80px', color: '#1f2937',
      fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif', fontSize: 15 }}>
      <h1 style={{ fontSize: 30, fontWeight: 800, margin: '0 0 4px', color: '#0f172a' }}>Privacy Policy</h1>
      <p style={{ color: '#64748b', margin: '0 0 24px', fontSize: 14 }}>Last updated: {UPDATED}</p>

      <p style={p}>
        This Privacy Policy describes how <strong>IT Solutions of LI Inc</strong> (&ldquo;IT Solutions of LI&rdquo;,
        &ldquo;we&rdquo;, &ldquo;us&rdquo;, or &ldquo;our&rdquo;), the developer of the <strong>MetricsPro</strong>
        application (the &ldquo;Service&rdquo;), collects, uses, and protects information when our authorized business
        customers and their staff use the Service. MetricsPro is a private, business-to-business operations and
        commission-analytics tool used by retail organizations to manage their own sales, commission, inventory,
        accounting, and staffing data. It is not a consumer-facing product and is accessible only to authorized users.
      </p>

      <h2 style={h2}>Information We Collect</h2>
      <p style={p}>We collect and process the following categories of information on behalf of our business customers:</p>
      <ul>
        <li style={li}><strong>Account information.</strong> Name, work email address, mobile phone number, assigned
          role, and the store/market a user is associated with, used to authenticate users and control access.</li>
        <li style={li}><strong>Business operational data.</strong> Sales transactions, commission and payout records,
          inventory and asset records, store performance metrics, accounting figures, and staffing/scheduling data that
          the business customer uploads to or connects with the Service.</li>
        <li style={li}><strong>Usage information.</strong> Basic logs needed to operate and secure the Service, such as
          sign-in events and report-delivery status.</li>
      </ul>
      <p style={p}>
        MetricsPro is used internally by businesses to analyze their own operations. We do not knowingly collect
        information from the general public, and the Service is not directed to children.
      </p>

      <h2 style={h2}>How We Use Information</h2>
      <ul>
        <li style={li}>To provide the Service&rsquo;s analytics, reporting, reconciliation, and dashboards.</li>
        <li style={li}>To authenticate users and enforce role-based access controls.</li>
        <li style={li}>To deliver reports and operational notifications that a user has chosen to receive, by email and,
          where the user has opted in, via WhatsApp.</li>
        <li style={li}>To maintain, secure, troubleshoot, and improve the Service.</li>
      </ul>

      <h2 style={h2}>WhatsApp and Email Notifications</h2>
      <p style={p}>
        Where a user opts in, MetricsPro can deliver reports and operational alerts to that user&rsquo;s registered
        mobile number using the WhatsApp Business Platform (provided by Meta), and to that user&rsquo;s email address.
        These messages are business notifications related to the recipient&rsquo;s own work (for example, a performance
        report or a reconciliation alert). We do not send marketing messages and we do not share recipient phone numbers
        with third parties for their own marketing. A recipient can opt out at any time by contacting us or their
        administrator, or by replying to stop messages where applicable. Messages sent through WhatsApp are also subject
        to Meta&rsquo;s own terms and privacy practices.
      </p>

      <h2 style={h2}>How We Share Information</h2>
      <p style={p}>
        We do not sell personal information. We share information only as needed to operate the Service, with
        infrastructure and service providers that process data on our behalf under appropriate confidentiality and
        security obligations. By category, those are: cloud database, authentication and file storage; application
        hosting; web hosting and content delivery; transactional email delivery; business messaging, for opt-in
        WhatsApp notifications; AI summarization, where a customer enables it, limited to the figures in the requested
        summary; and reviews and authorized integrations a customer chooses to connect. All process data in the United
        States, save for messaging and content delivery, which are global. We give the specific provider names to
        customers and their advisors on request; we do not publish a confirmed vendor list, because its main use to a
        stranger is impersonating one of them. We may also disclose information if required by law or to protect
        the rights, safety, and security of our customers, users, or the Service.
      </p>

      <h2 style={h2}>Data Retention</h2>
      <p style={p}>
        We retain business and account information for as long as the relevant business customer maintains its account,
        or as needed to provide the Service and comply with legal obligations. Upon a verified request from the business
        customer, we will delete or return the customer&rsquo;s data, subject to legal retention requirements.
      </p>

      <h2 style={h2}>Security</h2>
      <p style={p}>
        We use industry-standard safeguards to protect information, including encrypted transport, access controls, and
        restricted credentials. No method of transmission or storage is completely secure, but we work to protect data
        consistent with applicable requirements.
      </p>

      <h2 style={h2}>Your Rights</h2>
      <p style={p}>
        Because MetricsPro processes data on behalf of business customers, users should direct requests to access,
        correct, or delete their information to their organization&rsquo;s administrator, who can also contact us.
        Depending on your location, you may have additional rights under applicable privacy laws; we will honor valid
        requests as required by law.
      </p>

      <h2 style={h2}>Changes to This Policy</h2>
      <p style={p}>
        We may update this Privacy Policy from time to time. Material changes will be reflected by updating the
        &ldquo;Last updated&rdquo; date above.
      </p>

      <h2 style={h2}>Contact Us</h2>
      <p style={p}>
        If you have questions about this Privacy Policy or our data practices, contact us at:
      </p>
      <p style={p}>
        <strong>IT Solutions of LI Inc</strong><br />
        Email: <a href="mailto:sales@itsolutionsli.com" style={{ color: '#2563eb' }}>sales@itsolutionsli.com</a>
      </p>
    </main>
  )
}
