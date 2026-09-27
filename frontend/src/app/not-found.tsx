import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Page not found · MetricsPro' }

// The 404 (owner 2026-09-27, "no custom 404"). Next's default is an unstyled black-on-white
// "404 | This page could not be found", which inside a signed-in product reads as a broken app
// rather than a wrong address.
//
// IT SAYS WHAT TO DO NEXT, which is the only thing a 404 is for. It does NOT guess where the person
// meant to go: a redirect to a dashboard hides the broken link that brought them here, and a stale
// bookmark would then look like it still works. It also does not echo the requested path back onto
// the page — a 404 that renders arbitrary URL text is a reflected-XSS shape, and this app has no
// other one (see the security audit: zero dangerouslySetInnerHTML).
export default function NotFound() {
  return (
    <main style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 24, fontFamily: 'var(--font-inter), system-ui, sans-serif', background: '#f8fafc',
    }}>
      <div style={{
        maxWidth: 460, width: '100%', background: '#fff', border: '1px solid #e2e8f0',
        borderRadius: 12, padding: '32px 28px', textAlign: 'center',
      }}>
        <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 1, color: '#94a3b8' }}>404</div>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 10px', color: '#0f172a' }}>
          That page isn’t here
        </h1>
        <p style={{ fontSize: 14, lineHeight: 1.6, color: '#475569', margin: '0 0 22px' }}>
          The address may be mistyped, or the screen may have moved. If you followed a link from
          inside MetricsPro, that link is the thing that’s wrong — worth telling an admin so it gets
          fixed rather than worked around.
        </p>
        <Link href="/" style={{
          display: 'inline-block', background: '#0f172a', color: '#fff', textDecoration: 'none',
          padding: '10px 20px', borderRadius: 8, fontSize: 14, fontWeight: 600,
        }}>
          Go to my dashboard
        </Link>
      </div>
    </main>
  )
}
