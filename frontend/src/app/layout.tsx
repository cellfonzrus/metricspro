import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { AuthProvider } from '@/lib/auth-context'
import UnsafeLinkGuard from '@/components/UnsafeLinkGuard'
import { siteUrl } from '@/lib/apiBase'
import { DEFAULT_THEME, PRE_PAINT, THEME_ATTR } from '@/lib/theme'

// Load Inter for real (2026-08-29 design polish). The stylesheet has named 'Inter' as the UI face since
// day one, but nothing ever LOADED it — every screen silently fell back to system-ui. next/font self-hosts
// the variable font (no external request at runtime, no layout shift) and exposes it as `--font-inter`,
// which globals.css uses as the body family. `tabular-nums` on the same face is what lines the numbers up.
const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' })

// Branding (2026-09-24). icon.svg / apple-icon.png / favicon.ico live beside this file and the app
// router wires them automatically — they are the same Fold mark the marketing site serves, copied
// rather than re-drawn so the two can never diverge. metadataBase makes the og:image absolute,
// which is what link previews require; without it Next emits a relative path and previews go blank.
// The customer-facing site origin — ONE home (lib/apiBase.ts, index §40), shared with the canonical-host
// redirect in next.config.ts so the two can never disagree.
const SITE = siteUrl()

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: 'MetricsPro — Commission Intelligence',
  description: 'Commissions, point of sale, inventory, workforce and cash — reconciled against each other.',
  openGraph: {
    type: 'website',
    siteName: 'MetricsPro',
    title: 'MetricsPro — Commission Intelligence',
    description: 'Commissions, point of sale, inventory, workforce and cash — reconciled against each other.',
    images: [{ url: '/og.png', width: 1200, height: 630 }],
  },
  twitter: { card: 'summary_large_image' },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} {...{ [THEME_ATTR]: DEFAULT_THEME }}>
      {/* The theme is applied BEFORE the first paint. Left to an effect, the browser would paint the
          server's default and then repaint — on this app a white flash in front of a black page. */}
      <head><script dangerouslySetInnerHTML={{ __html: PRE_PAINT }} /></head>
      {/* UnsafeLinkGuard: app-wide `javascript:`/`vbscript:`/`data:text/html` click net (H6,
          2026-08-05 audit). Renders nothing; see the component header for why it is a deny-list. */}
      <body><AuthProvider><UnsafeLinkGuard />{children}</AuthProvider></body>
    </html>
  )
}
