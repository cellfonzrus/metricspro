import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'MetricsPro — Commission Intelligence',
  description: 'Commission Intelligence & Business Operations',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
