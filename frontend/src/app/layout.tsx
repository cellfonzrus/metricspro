import type { Metadata } from 'next'
import './globals.css'
import { AuthProvider } from '@/lib/auth-context'
import UnsafeLinkGuard from '@/components/UnsafeLinkGuard'

export const metadata: Metadata = {
  title: 'MetricsPro — Commission Intelligence',
  description: 'Commission Intelligence & Business Operations',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* UnsafeLinkGuard: app-wide `javascript:`/`vbscript:`/`data:text/html` click net (H6,
          2026-08-05 audit). Renders nothing; see the component header for why it is a deny-list. */}
      <body><AuthProvider><UnsafeLinkGuard />{children}</AuthProvider></body>
    </html>
  )
}
