'use client'
import { useState, useRef, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, ORG_ID } from '@/lib/client'
import DataAnswer, { type DataChart, type DataTable, type DataSource } from '@/components/DataAnswer'

// The in-app assistant. ONE panel, TWO doors — and it is deliberately one panel (index §52):
//
//   "Your data"  -> POST /core/data-qa   (index §52) — questions about THIS tenant's numbers. The
//                   backend runs the platform's OWN reports as the signed-in user and does the
//                   arithmetic in proved code, so a figure here is the same figure as the report's.
//   "How to"     -> POST /helpdesk/ai-assist — the product / how-to assistant that already existed.
//                   It says in its own system prompt that it has no database access, which is exactly
//                   why it could never answer "which store was best" and why the data door exists.
//
// WHY NOT A SECOND WIDGET. The duplicate-check gate: the product already had an "Ask AI" panel and a
// platform-wide AskBar. Adding a third place to ask a question would mean three answers to the same
// question, which is the drift the house rules forbid. So the EXISTING panel grew a door, the
// EXISTING AskBar hands its typed question to it, and there is still one Ask AI in the product.
//
// WHAT THE PANEL RENDERS. The data door returns a sentence plus the pivot tables and charts the
// backend built; <DataAnswer> formats them and names the reports they were read from. This component
// computes no figure of its own — if it did, the screen could disagree with the sentence above it.
type Msg = {
  role: 'user' | 'assistant'
  content: string
  charts?: DataChart[]
  tables?: DataTable[]
  sources?: DataSource[]
}
type Mode = 'data' | 'howto'

// Client-side ceiling on ONE round trip. The backend caps its own model call and always answers
// gracefully; this is the belt-and-braces so a network/proxy stall can never leave the user staring
// at "thinking…" forever (SEV-1 2026-07-30). The data door runs a TOOL LOOP — it may read a report,
// pivot it, then answer — so it legitimately takes longer than a one-shot how-to answer.
const HOWTO_TIMEOUT_MS = 60_000
const DATA_TIMEOUT_MS = 150_000
const SLOW_MSG = 'The assistant is taking too long — please try again in a minute, or raise a ticket and a person will help.'

// What the assistant's POST returns: a sentence, plus the tables and charts the BACKEND built.
type AnswerPayload = {
  reply?: string
  charts?: DataChart[]
  tables?: DataTable[]
  sources?: DataSource[]
}

type DataStatus = { module_enabled: boolean; configured: boolean; allowed: boolean; reason?: string | null
                    questions?: { question: string; label: string; answers: string }[] }
type HowtoStatus = { module_enabled: boolean; configured: boolean }

export default function AiAssistant({ initialOpen = false, initialQuestion = '', compact = false,
                                      initialMode }: {
  initialOpen?: boolean; initialQuestion?: string; compact?: boolean
  /** Which door a handed-off question belongs to, decided by `search-rank.askDoor` (§54.8). The
   *  caller names it; this component still falls back to the door the tenant is actually entitled
   *  to, so a routing opinion can never produce a refusal where an answer was available. */
  initialMode?: Mode
}) {
  const [open, setOpen] = useState(!!initialOpen)
  const [mode, setMode] = useState<Mode>('data')
  const [howto, setHowto] = useState<HowtoStatus | null>(null)
  const [data, setData] = useState<DataStatus | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const sentInitial = useRef(false)

  useEffect(() => {
    api(`/api/v1/helpdesk/ai-assist/status?org_id=${ORG_ID}`)
      .then((d: Partial<HowtoStatus>) =>
        setHowto({ module_enabled: !!d.module_enabled, configured: !!d.configured }))
      .catch(() => setHowto({ module_enabled: false, configured: false }))
    api(`/api/v1/core/data-qa/status?org_id=${ORG_ID}`)
      .then((d: Partial<DataStatus>) => {
        setData({ module_enabled: !!d.module_enabled, configured: !!d.configured, allowed: !!d.allowed,
                  reason: d.reason || null, questions: Array.isArray(d.questions) ? d.questions : [] })
        // A login that may not use the data door opens on the how-to door rather than on a refusal.
        if (!d.allowed) setMode('howto')
      })
      .catch(() => setData({ module_enabled: false, configured: false, allowed: false }))
  }, [])
  useEffect(() => { if (open) endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, open])

  const send = useCallback(async (text?: string, forceMode?: Mode) => {
    const q = (text ?? input).trim()
    if (!q || busy) return
    const use: Mode = forceMode || mode
    setErr(''); if (text === undefined) setInput('')
    // Only the TEXT of prior turns is replayed, never a rendered table — the backend re-reads the
    // report for itself, so a stale number can never be carried forward as if it were current.
    const history = msgs.slice(-8).map(m => ({ role: m.role, content: m.content }))
    setMsgs(m => [...m, { role: 'user', content: q }])
    setBusy(true)
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), use === 'data' ? DATA_TIMEOUT_MS : HOWTO_TIMEOUT_MS)
    try {
      const path = use === 'data'
        ? `/api/v1/core/data-qa?org_id=${ORG_ID}`
        : `/api/v1/helpdesk/ai-assist?org_id=${ORG_ID}`
      const d = await api(path, {
        method: 'POST', body: JSON.stringify({ message: q, history }), signal: ctrl.signal,
      }) as AnswerPayload
      setMsgs(m => [...m, {
        role: 'assistant', content: d.reply || '(no answer)',
        charts: d.charts || undefined, tables: d.tables || undefined, sources: d.sources || undefined,
      }])
    } catch (e: unknown) {
      const ex = e as { name?: string; message?: string } | undefined
      const timedOut = ex?.name === 'AbortError' || ctrl.signal.aborted
      setErr(timedOut ? SLOW_MSG : (ex?.message || 'The assistant is unavailable right now.'))
      setMsgs(m => [...m, { role: 'assistant', content: timedOut ? SLOW_MSG
        : 'Sorry — I hit an error. You can raise a ticket and a person will help.' }])
    } finally { clearTimeout(timer); setBusy(false) }
  }, [input, busy, mode, msgs])

  // An AskBar hand-off: the question the user already typed is asked once, on the data door, as soon
  // as the status says it may be. Guarded by a ref so a re-render never re-asks (and re-spends).
  useEffect(() => {
    if (sentInitial.current || !initialQuestion || !data) return
    sentInitial.current = true
    // The caller's routing is honoured when the tenant may use that door; otherwise the entitled
    // door answers. A question routed to 'howto' is asked there even when the data door is open —
    // that IS the fix: "how do I reset a password" has no answer in the reports.
    const want: Mode = initialMode || 'data'
    const use: Mode = want === 'howto'
      ? (howto?.module_enabled && howto?.configured ? 'howto' : (data.allowed ? 'data' : 'howto'))
      : (data.allowed ? 'data' : 'howto')
    setMode(use)
    void send(initialQuestion, use)
  }, [initialQuestion, data, howto, initialMode, send])

  const dataOK = !!data?.allowed
  // Unchanged contract: a tenant entitled to neither assistant sees nothing at all.
  if (howto && data && !howto.module_enabled && !data.module_enabled) return null

  const notConfigured = (mode === 'data' ? data && !data.configured : howto && !howto.configured)
  const examples = (data?.questions || []).slice(0, 3).map(q => q.label)

  const body = (
    <div style={{ padding: compact ? 12 : 14, borderTop: compact ? 'none' : '1px solid var(--border)' }}>
      {/* The two doors, named in the user's terms. One panel, so there is one place to ask. */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        {([['data', 'Your data'], ['howto', 'How to use MetricsPro']] as [Mode, string][]).map(([m, label]) => {
          const disabled = m === 'data' ? !dataOK : !howto?.module_enabled
          return (
            <button key={m} onClick={() => !disabled && setMode(m)} disabled={disabled}
              title={m === 'data' && !dataOK ? (data?.reason || 'Not available for this login') : undefined}
              style={{ fontSize: 12, padding: '4px 10px', borderRadius: 999, cursor: disabled ? 'not-allowed' : 'pointer',
                border: '1px solid ' + (mode === m ? '#2563eb' : 'var(--border)'),
                background: mode === m ? '#2563eb' : 'transparent',
                color: mode === m ? '#fff' : (disabled ? 'var(--text3)' : 'var(--text1)'),
                opacity: disabled ? 0.55 : 1 }}>{label}</button>
          )
        })}
      </div>

      {mode === 'data' && !dataOK && data && (
        <div style={{ fontSize: 13, color: '#b45309', marginBottom: 10 }}>
          {data.reason || 'The data assistant is not available for this login.'}
        </div>
      )}
      {notConfigured && (
        <div style={{ fontSize: 13, color: '#b45309', marginBottom: 10 }}>
          The assistant isn’t configured yet — ask an admin to set the API key. You can still{' '}
          <Link href="/helpdesk/new" style={{ color: '#2563eb' }}>raise a ticket</Link>.
        </div>
      )}

      <div style={{ maxHeight: compact ? 300 : 420, overflowY: 'auto', display: 'flex', flexDirection: 'column',
        gap: 10, marginBottom: 10 }}>
        {msgs.length === 0 && (
          <div style={{ fontSize: 13, color: 'var(--text3)' }}>
            {mode === 'data'
              // The suggestions come from the registry, so they GROW as the tenant's modules come on
              // rather than being a hard-coded list that goes stale (index §52.5).
              ? <>Try: “Which store was my best last month and what revenue did it make?” · “Who is my
                  best salesperson?” · “Who is pulling me down?” · “What do I need to pull sales up?”
                  {examples.length > 0 && <div style={{ marginTop: 6, fontSize: 12 }}>
                    I can read: {examples.join(' · ')}{(data?.questions?.length || 0) > 3
                      ? ` and ${(data!.questions!.length) - 3} more` : ''}.
                  </div>}</>
              : 'Try: “How do I upload sales?” · “Why is my discrepancy empty?” · “Where do I see a rep’s payout?”'}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: m.role === 'user' ? '85%' : '100%', width: m.role === 'user' ? undefined : '100%' }}>
            <div style={{ background: m.role === 'user' ? '#2563eb' : 'var(--surface)',
              color: m.role === 'user' ? '#fff' : 'var(--text1)',
              border: m.role === 'user' ? 'none' : '1px solid var(--border)', borderRadius: 12,
              padding: '8px 12px', fontSize: 14, whiteSpace: 'pre-wrap' }}>{m.content}</div>
            {m.role === 'assistant' && (
              <DataAnswer charts={m.charts} tables={m.tables} sources={m.sources} />
            )}
          </div>
        ))}
        {busy && (
          <div style={{ alignSelf: 'flex-start', fontSize: 13, color: 'var(--text3)' }}>
            {mode === 'data' ? 'reading your reports…' : 'thinking…'}
          </div>
        )}
        <div ref={endRef} />
      </div>
      {err && <div style={{ fontSize: 12, color: '#c0392b', marginBottom: 6 }}>{err}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <input className="input" style={{ flex: 1 }} value={input} disabled={busy}
          placeholder={mode === 'data' ? 'Ask about your numbers…' : 'Ask a question…'}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') void send() }} />
        <button className="btn btn-primary" disabled={busy || !input.trim()} onClick={() => void send()}>
          {busy ? '…' : 'Send'}
        </button>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
        {mode === 'data'
          // Said plainly, because it is the property that makes the answers trustworthy.
          ? 'Read-only. Figures come from your own reports, scoped to what you can see — the same numbers as the report pages.'
          : <>Read-only — it can’t change data or money. For that, use the relevant screen or{' '}
            <Link href="/helpdesk/new" style={{ color: '#2563eb' }}>raise a ticket</Link>.</>}
      </div>
    </div>
  )

  if (compact) return body

  return (
    <div className="card" style={{ padding: 0, marginBottom: 12, overflow: 'hidden', borderColor: '#c7d2fe' }}>
      <button onClick={() => setOpen(o => !o)} style={{ width: '100%', textAlign: 'left', background: 'none',
        border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px' }}>
        <span style={{ fontSize: 18 }}>🤖</span>
        <span style={{ fontWeight: 700 }}>Ask AI</span>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          — your numbers and how-to answers, scoped to your company
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--text3)' }}>{open ? '▲' : '▼'}</span>
      </button>
      {open && body}
    </div>
  )
}
