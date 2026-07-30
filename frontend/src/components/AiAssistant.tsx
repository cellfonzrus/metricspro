'use client'
import { useState, useRef, useEffect } from 'react'
import Link from 'next/link'
import { api, ORG_ID } from '@/lib/client'

// In-app AI support assistant (Phase 2). Tenant-scoped, READ-ONLY — talks to /helpdesk/ai-assist.
// The PARENT gates rendering on the ai_assistant role permission; this component also checks the
// tenant entitlement + API-key status and degrades gracefully.
type Msg = { role: 'user' | 'assistant'; content: string }

// Client-side ceiling on one Ask-AI round trip. The backend caps its own model call at ~60s
// (AI_ASSIST_TIMEOUT_S x retries) and always answers gracefully; this is the belt-and-braces so a
// network/proxy stall can never leave the user staring at "thinking…" forever. (SEV-1 2026-07-30.)
const AI_CLIENT_TIMEOUT_MS = 60_000
const AI_SLOW_MSG = 'The assistant is taking too long — please try again in a minute, or raise a ticket and a person will help.'

export default function AiAssistant() {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<{ module_enabled: boolean; configured: boolean } | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api(`/api/v1/helpdesk/ai-assist/status?org_id=${ORG_ID}`)
      .then((d: any) => setStatus({ module_enabled: !!d.module_enabled, configured: !!d.configured }))
      .catch(() => setStatus({ module_enabled: false, configured: false }))
  }, [])
  useEffect(() => { if (open) endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, open])

  async function send() {
    const q = input.trim()
    if (!q || busy) return
    setErr(''); setInput('')
    const history = msgs.slice(-10)
    setMsgs(m => [...m, { role: 'user', content: q }])
    setBusy(true)
    // api() spreads its opts straight into fetch(), so an AbortSignal passes through unchanged —
    // no change to the shared client is needed.
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), AI_CLIENT_TIMEOUT_MS)
    try {
      const d = await api(`/api/v1/helpdesk/ai-assist?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ message: q, history }), signal: ctrl.signal,
      })
      setMsgs(m => [...m, { role: 'assistant', content: d.reply || '(no answer)' }])
    } catch (e: any) {
      const timedOut = e?.name === 'AbortError' || ctrl.signal.aborted
      setErr(timedOut ? AI_SLOW_MSG : (e?.message || 'The assistant is unavailable right now.'))
      setMsgs(m => [...m, { role: 'assistant', content: timedOut ? AI_SLOW_MSG
        : 'Sorry — I hit an error. You can raise a ticket and a person will help.' }])
    } finally { clearTimeout(timer); setBusy(false) }
  }

  if (status && !status.module_enabled) return null   // tenant not entitled to the AI assistant

  return (
    <div className="card" style={{ padding: 0, marginBottom: 12, overflow: 'hidden', borderColor: '#c7d2fe' }}>
      <button onClick={() => setOpen(o => !o)} style={{ width: '100%', textAlign: 'left', background: 'none', border: 'none',
        cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px' }}>
        <span style={{ fontSize: 18 }}>🤖</span>
        <span style={{ fontWeight: 700 }}>Ask AI</span>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>— quick answers about MetricsPro, scoped to your company</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--text3)' }}>{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div style={{ borderTop: '1px solid var(--border)', padding: 14 }}>
          {status && !status.configured && (
            <div style={{ fontSize: 13, color: '#b45309', marginBottom: 10 }}>
              The AI assistant isn’t configured yet — ask an admin to set the API key. You can still{' '}
              <Link href="/helpdesk/new" style={{ color: '#2563eb' }}>raise a ticket</Link>.
            </div>
          )}
          <div style={{ maxHeight: 320, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
            {msgs.length === 0 && (
              <div style={{ fontSize: 13, color: 'var(--text3)' }}>
                Try: “How do I upload sales?” · “Why is my discrepancy empty?” · “Where do I see a rep’s payout?”
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '85%',
                background: m.role === 'user' ? '#2563eb' : 'var(--surface)', color: m.role === 'user' ? '#fff' : 'var(--text1)',
                border: m.role === 'user' ? 'none' : '1px solid var(--border)', borderRadius: 12, padding: '8px 12px',
                fontSize: 14, whiteSpace: 'pre-wrap' }}>{m.content}</div>
            ))}
            {busy && <div style={{ alignSelf: 'flex-start', fontSize: 13, color: 'var(--text3)' }}>thinking…</div>}
            <div ref={endRef} />
          </div>
          {err && <div style={{ fontSize: 12, color: '#c0392b', marginBottom: 6 }}>{err}</div>}
          <div style={{ display: 'flex', gap: 8 }}>
            <input className="input" style={{ flex: 1 }} placeholder="Ask a question…" value={input}
              onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') send() }} disabled={busy} />
            <button className="btn btn-primary" disabled={busy || !input.trim()} onClick={send}>{busy ? '…' : 'Send'}</button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
            Read-only — it can’t change data or money. For that, use the relevant screen or{' '}
            <Link href="/helpdesk/new" style={{ color: '#2563eb' }}>raise a ticket</Link>.
          </div>
        </div>
      )}
    </div>
  )
}
