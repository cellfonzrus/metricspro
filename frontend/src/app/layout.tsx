import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { AuthProvider } from '@/lib/auth-context'
import UnsafeLinkGuard from '@/components/UnsafeLinkGuard'

// Load Inter for real (2026-08-29 design polish). The stylesheet has named 'Inter' as the UI face since
// day one, but nothing ever LOADED it — every screen silently fell back to system-ui. next/font self-hosts
// the variable font (no external request at runtime, no layout shift) and exposes it as `--font-inter`,
// which globals.css uses as the body family. `tabular-nums` on the same face is what lines the numbers up.
const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' })

// Branding (2026-09-24). icon.svg / apple-icon.png / favicon.ico live beside this file and the app
// router wires them automatically — they are the same Fold mark the marketing site serves, copied
// rather than re-drawn so the two can never diverge. metadataBase makes the og:image absolute,
// which is what link previews require; without it Next emits a relative path and previews go blank.
const SITE = process.env.NEXT_PUBLIC_SITE_URL || 'https://metricspro.tech'

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
    <html lang="en" className={inter.variable}>
      {/* UnsafeLinkGuard: app-wide `javascript:`/`vbscript:`/`data:text/html` click net (H6,
          2026-08-05 audit). Renders nothing; see the component header for why it is a deny-list. */}
      <body><AuthProvider><UnsafeLinkGuard />{children}</AuthProvider></body>
    </html>
  )
}
