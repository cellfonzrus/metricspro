'use client'
import { useEffect } from 'react'

// The error boundary (owner 2026-09-27, "generic error messages"). Without this file a thrown render
// error takes the whole route down to Next's default screen, and in production that screen says
// nothing — the person sees a blank page and reports "it broke".
//
// WHAT IT SHOWS, AND WHAT IT DELIBERATELY DOES NOT. `error.message` is NOT rendered. In this app a
// thrown error can carry a row from a commission query, a store's cash figure or a rep's name, and a
// crash screen is the one surface nobody remembers to check for leakage. What the person gets is the
// `digest` — the identifier Next also writes to the server log — so support can find the exact trace
// without the page itself becoming the disclosure. A crash is exactly when a product is most tempted
// to over-share.
//
// `reset()` re-renders the segment rather than reloading the window, so unsaved filter state on the
// surrounding layout survives a transient failure.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Server-side logging already has the trace; this is for the browser console during support
    // calls. console.error, never console.log — the lint/ratchet convention in this codebase.
    console.error('[MetricsPro] route error', error?.digest || '(no digest)')
  }, [error])

  return (
    <main style={{
      minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      fontFamily: 'var(--font-inter), system-ui, sans-serif',
    }}>
      <div style={{
        maxWidth: 480, width: '100%', background: '#fff', border: '1px solid #e2e8f0',
        borderRadius: 12, padding: '28px 26px',
      }}>
        <h1 style={{ fontSize: 19, fontWeight: 700, margin: '0 0 10px', color: '#0f172a' }}>
          This screen didn’t load
        </h1>
        <p style={{ fontSize: 14, lineHeight: 1.6, color: '#475569', margin: '0 0 8px' }}>
          Something failed while building this page. Your data is untouched — nothing was saved or
          changed by this error.
        </p>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: '#64748b', margin: '0 0 20px' }}>
          Try again below. If it keeps happening, send an admin the reference
          {' '}<code style={{
            background: '#f1f5f9', padding: '2px 6px', borderRadius: 4, fontSize: 12.5,
          }}>{error?.digest || 'unavailable'}</code>{' '}
          — it points at the exact server log entry.
        </p>
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={reset} style={{
            background: '#0f172a', color: '#fff', border: 'none', padding: '9px 18px',
            borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer',
          }}>Try again</button>
          <a href="/" style={{
            background: '#f1f5f9', color: '#0f172a', textDecoration: 'none', padding: '9px 18px',
            borderRadius: 8, fontSize: 14, fontWeight: 600,
          }}>Back to dashboard</a>
        </div>
      </div>
    </main>
  )
}
