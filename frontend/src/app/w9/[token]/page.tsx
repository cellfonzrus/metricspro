'use client'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import { apiUrl } from '@/lib/apiBase'

// PUBLIC W-9 RETURN PAGE (owner 2026-10-30, index §POS-16) — reached by the link emailed to a
// wholesale customer. NO login: the one-use token in the URL is the only credential, it reaches
// only that one customer's upload slot, it expires, and it is cleared the moment the form arrives.
// Talks ONLY to the token-guarded /pos/public/w9 endpoints. Lives outside the (platform) RBAC group
// on purpose, like /onboard.
//
// It shows the company that is asking, a link to the blank form, and one upload box. It shows
// NOTHING from the customer's record — not their name, not their email, not what they have bought
// — because a link in an inbox can be forwarded, and anything on this page travels with it.

const card: React.CSSProperties = { background: 'var(--c-bg-ffffff)', border: '1px solid var(--c-bg-e5e7eb)', borderRadius: 12, padding: 24, maxWidth: 560, margin: '0 auto' }
const btnP: React.CSSProperties = { padding: '10px 16px', borderRadius: 8, border: 'none', background: 'var(--c-bg-2563eb)', color: 'var(--c-fg-ffffff)', fontSize: 15, fontWeight: 600, cursor: 'pointer' }

type Meta = { ok: boolean; company_name: string; form_url: string; accepted_formats: string[]; what_we_need: string }

const EXT_FOR: Record<string, string[]> = { pdf: ['.pdf'], jpeg: ['.jpg', '.jpeg'], png: ['.png'] }
function acceptAttr(formats?: string[]): string {
  return (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || []).join(',')
}

export default function W9ReturnPage() {
  const params = useParams<{ token: string }>()
  const token = String(params?.token || '')
  const [meta, setMeta] = useState<Meta | null>(null)
  const [dead, setDead] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let live = true
    fetch(apiUrl(`/api/v1/pos/public/w9/${encodeURIComponent(token)}`))
      .then(async r => {
        const body = await r.json().catch(() => ({}))
        if (!live) return
        // An unknown, expired or already-used link all answer the same thing, so this page cannot
        // tell the visitor which it was either.
        if (!r.ok) setDead(body?.detail || 'This upload link is no longer valid.')
        else setMeta(body as Meta)
      })
      .catch(() => { if (live) setDead('This upload link could not be opened. Please try again shortly.') })
    return () => { live = false }
  }, [token])

  async function send() {
    const file = fileRef.current?.files?.[0]
    if (!file) { setError('Choose the completed form first.'); return }
    setBusy(true); setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const r = await fetch(apiUrl(`/api/v1/pos/public/w9/${encodeURIComponent(token)}/upload`),
        { method: 'POST', body: form })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) setError(body?.detail || 'That file could not be accepted.')
      else setDone(body?.message || 'Thank you — your Form W-9 has been sent.')
    } catch {
      setError('That could not be sent. Please check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main style={{ padding: '48px 16px', minHeight: '100vh', background: 'var(--c-bg-f8fafc)' }}>
      <div style={card}>
        {dead && <p style={{ fontSize: 15, margin: 0 }}>{dead}</p>}

        {!dead && !meta && <p style={{ fontSize: 15, margin: 0 }}>Opening…</p>}

        {!dead && meta && done && (
          <>
            <h1 style={{ fontSize: 20, margin: '0 0 12px' }}>Thank you</h1>
            <p style={{ fontSize: 15, margin: 0 }}>{done}</p>
          </>
        )}

        {!dead && meta && !done && (
          <>
            <h1 style={{ fontSize: 20, margin: '0 0 8px' }}>
              {meta.company_name ? `${meta.company_name} needs your Form W-9` : 'Form W-9 requested'}
            </h1>
            <p style={{ fontSize: 15, color: 'var(--c-fg-334155)', margin: '0 0 20px' }}>
              Two steps, and you are done. We need {meta.what_we_need}.
            </p>

            <p style={{ fontSize: 15, margin: '0 0 8px' }}><strong>1.</strong> Download the blank form:</p>
            <p style={{ margin: '0 0 20px' }}>
              <a href={meta.form_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 15 }}>
                Form W-9 (PDF)
              </a>
            </p>

            <p style={{ fontSize: 15, margin: '0 0 8px' }}>
              <strong>2.</strong> Fill it in, sign it, and send it back here
              {meta.accepted_formats?.length
                ? ` (${meta.accepted_formats.map(f => f.toUpperCase()).join(' or ')})`
                : ''}:
            </p>
            <input ref={fileRef} type="file" accept={acceptAttr(meta.accepted_formats)}
                   style={{ fontSize: 15, margin: '0 0 16px', display: 'block' }} />
            {error && <p style={{ fontSize: 14, color: 'var(--c-fg-b91c1c)', margin: '0 0 12px' }}>{error}</p>}
            <button style={{ ...btnP, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={send}>
              {busy ? 'Sending…' : 'Send my W-9'}
            </button>
            <p style={{ fontSize: 13, color: 'var(--c-fg-334155)', margin: '20px 0 0' }}>
              This link is personal to you and can be used once. If it has expired, ask us for a new one.
            </p>
          </>
        )}
      </div>
    </main>
  )
}
