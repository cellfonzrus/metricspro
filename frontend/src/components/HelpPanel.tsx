'use client'
// HelpPanel — the user-facing "?" help button mounted in the platform header (mig 715 tech-support).
// Resolves the CURRENT pathname against GET /api/v1/core/support-doc/resolve and slides out a trimmed,
// user-facing help view (user_md rendered as light markdown) with a "Contact support" button that
// deep-links to the helpdesk new-ticket flow with page context prefilled.
//
// FAIL-SILENT by contract: no doc → "help is coming soon" + the contact button; ANY fetch error → the
// panel simply shows the fallback, never throwing. It must never break the page it sits on. Uses an
// explicit /api/v1 path (a bare path 404s silently in the UI).
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { api } from '@/lib/client'

// Minimal, SAFE markdown → JSX (no dangerouslySetInnerHTML): headings, bullets, blank-line spacing,
// inline **bold** and `code`. Anything unrecognized renders as plain text.
function inline(text: string, keyBase: string) {
  const parts: React.ReactNode[] = []
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`)/g
  let last = 0, m: RegExpExecArray | null, i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    if (m[2] !== undefined) parts.push(<strong key={`${keyBase}-b${i}`}>{m[2]}</strong>)
    else if (m[3] !== undefined) parts.push(<code key={`${keyBase}-c${i}`} style={{ background: 'var(--bg2)', padding: '1px 4px', borderRadius: 4, fontSize: 12 }}>{m[3]}</code>)
    last = m.index + m[0].length; i++
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}
function Markdown({ md }: { md: string }) {
  const lines = String(md || '').split('\n')
  const out: React.ReactNode[] = []
  let bullets: React.ReactNode[] = []
  const flush = () => {
    if (bullets.length) {
      out.push(<ul key={`ul-${out.length}`} style={{ margin: '4px 0 8px', paddingLeft: 18 }}>{bullets}</ul>)
      bullets = []
    }
  }
  lines.forEach((raw, idx) => {
    const line = raw.replace(/\r$/, '')
    if (/^\s*[-*]\s+/.test(line)) { bullets.push(<li key={`li-${idx}`} style={{ fontSize: 13, margin: '2px 0' }}>{inline(line.replace(/^\s*[-*]\s+/, ''), `li${idx}`)}</li>); return }
    flush()
    if (!line.trim()) { out.push(<div key={`sp-${idx}`} style={{ height: 6 }} />); return }
    const h = line.match(/^(#{1,3})\s+(.*)$/)
    if (h) { const lvl = h[1].length; out.push(<div key={`h-${idx}`} style={{ fontWeight: 700, fontSize: lvl === 1 ? 15 : 14, margin: '6px 0 2px' }}>{inline(h[2], `h${idx}`)}</div>); return }
    out.push(<div key={`p-${idx}`} style={{ fontSize: 13, lineHeight: 1.5, margin: '2px 0' }}>{inline(line, `p${idx}`)}</div>)
  })
  flush()
  return <>{out}</>
}

export default function HelpPanel() {
  const pathname = usePathname() || '/'
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [doc, setDoc] = useState<{ title?: string; user_md?: string } | null>(null)
  const [loadedPath, setLoadedPath] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api(`/api/v1/core/support-doc/resolve?path=${encodeURIComponent(pathname)}`)
      setDoc(r?.found ? (r.doc || null) : null)
    } catch {
      setDoc(null)   // FAIL-SILENT: any error → fallback, never throws
    } finally {
      setLoadedPath(pathname); setLoading(false)
    }
  }, [pathname])

  // Re-resolve when the panel opens (or the page changed while it was open).
  useEffect(() => { if (open && loadedPath !== pathname) load() }, [open, pathname, loadedPath, load])

  const contactHref = `/helpdesk/new?page=${encodeURIComponent(pathname)}&subject=${encodeURIComponent('Help with ' + pathname)}`

  return (
    <>
      <button onClick={() => setOpen(true)} title="Help for this page"
        aria-label="Open help" style={{ fontSize: 13, fontWeight: 700, color: 'var(--text2)',
          background: 'white', border: '1px solid var(--border)', borderRadius: 8, width: 30, height: 30,
          cursor: 'pointer', lineHeight: 1 }}>?</button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.25)', zIndex: 40 }} />
          <div style={{ position: 'fixed', top: 0, right: 0, height: '100vh', width: 380, maxWidth: '92vw',
            background: 'white', borderLeft: '1px solid var(--border)', boxShadow: '-8px 0 30px rgba(0,0,0,0.12)',
            zIndex: 41, display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex',
              alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>❓ {doc?.title || 'Help'}</div>
              <button onClick={() => setOpen(false)} aria-label="Close help"
                style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'var(--text3)' }}>×</button>
            </div>
            <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
              {loading ? (
                <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading help…</div>
              ) : doc?.user_md ? (
                <Markdown md={doc.user_md} />
              ) : (
                <div style={{ color: 'var(--text3)', fontSize: 13 }}>
                  Help for this page is coming soon. If you're stuck, contact support and a specialist will help.
                </div>
              )}
            </div>
            <div style={{ padding: 14, borderTop: '1px solid var(--border)' }}>
              <Link href={contactHref} onClick={() => setOpen(false)} className="btn btn-primary"
                style={{ display: 'block', textAlign: 'center', textDecoration: 'none' }}>
                💬 Contact support
              </Link>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6, textAlign: 'center' }}>
                Opens a ticket with this page attached.
              </div>
            </div>
          </div>
        </>
      )}
    </>
  )
}
