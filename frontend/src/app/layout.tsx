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

export const metadata: Metadata = {
  title: 'MetricsPro — Commission Intelligence',
  description: 'Commission Intelligence & Business Operations',
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
