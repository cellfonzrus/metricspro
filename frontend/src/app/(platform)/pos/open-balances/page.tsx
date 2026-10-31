'use client'
// OPEN TERMS BALANCES — what customers still owe us, and how late (index §POS-19).
//
// Owner 2026-10-31, after the Terms tender went live: "build them".
//
// NO ARITHMETIC HERE. Every figure, state, band and sentence is the server's, from the ONE home
// that owns the question (backend/app/modules/pos/receivable.py over
// backend/app/modules/pos/sale_settlement.py). This page does not subtract a balance, does not age
// a row against the browser's clock and does not decide what "61-90 days" means — a second answer
// to a money question on a screen is the duplicate defect the house rules forbid.
//
// WHAT IT REFUSES TO SHOW. A sale with no deferred leg is not a receivable of $0.00; it is not a
// receivable, and it is not on this list. 4,603 of the 4,605 live sales are imported receipt
// history with no payment row at all (measured 2026-10-31), and a screen that listed them as debts
// due today would be a collections queue over the whole sales ledger.
//
// AND WHAT IT SAYS OUT LOUD. Taking a payment against an open balance is not built yet, so a sale
// stays here until somebody edits it. That sentence is the SERVER's (`collection_note`) and is
// shown rather than worked around: a report that looked complete would have a manager chasing
// customers who had already paid.
//
// RULE FOUR (exports) — it renders through ReportShell, so Excel / PDF / print and the 📤 Send bar
// come for free and respect the ACTIVE filters.
// RULE FIVE (filters) — the standard bar's store / rep / date dimensions all exist for a sale, so
// they are the standard ones; `view` adds the question only this report asks.
import { useCallback, useEffect, useMemo, useState } from 'react'
import DateField from '@/components/DateField'
import ReportShell from '@/components/ReportShell'
import { api } from '@/lib/client'
import { PlatformDetail } from '@/lib/setupNotice'

type Row = {
  sale_id: string; transaction_id: number | null; created_at: string | null
  store_code: string | null; employee_name: string | null
  customer_name: string | null; customer_phone: string | null
  total: number | null; state: string; terms_days: number | null; due_date: string | null
  deferred: number; outstanding: number; days_past_due: number | null
  band: string | null; voided: boolean; tenders: string; note: string
}
type Summary = {
  sales: number; receivables: number; not_receivable: number; cleared: number; voided: number
  open_total: number; not_due_total: number; past_due_total: number; past_due_count: number
  oldest_days_past_due: number | null; bands: Record<string, number>
}
type Payload = {
  rows: Row[]; summary: Summary; available: boolean; setup_note: string | null
  as_of: string; view: string; collection_note: string; bands: string[]
}

const EMPTY: Payload = {
  rows: [], available: true, setup_note: null, as_of: '', view: 'open', collection_note: '',
  bands: [],
  summary: { sales: 0, receivables: 0, not_receivable: 0, cleared: 0, voided: 0, open_total: 0,
             not_due_total: 0, past_due_total: 0, past_due_count: 0, oldest_days_past_due: null,
             bands: {} },
}

const VIEWS: { key: string; label: string; note: string }[] = [
  { key: 'open', label: 'Open', note: 'Everything still owed, due or not.' },
  { key: 'past_due', label: 'Past due', note: 'Money that was due and has not come in.' },
  { key: 'not_due', label: 'Not due yet', note: 'Money owed to us inside the agreed terms.' },
  { key: 'all', label: 'Every terms sale', note: 'Including the ones already cleared or voided.' },
]

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? '—'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)

// Ninety days back: long enough to hold a 60-day term that has gone late, short enough that the
// default load is never the whole ledger.
const day = (back: number) =>
  new Date(Date.now() - back * 86400000).toISOString().slice(0, 10)

const STATE_WORDS: Record<string, string> = {
  past_due: 'Past due', not_due: 'Not due yet', cleared: 'Cleared', voided: 'Voided',
}

export default function OpenBalancesPage() {
  const [from, setFrom] = useState(day(90))
  const [to, setTo] = useState(day(0))
  const [store, setStore] = useState('')
  const [view, setView] = useState('open')
  const [data, setData] = useState<Payload>(EMPTY)
  const [err, setErr] = useState('')
  const [nonce, setNonce] = useState(0)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  const key = useMemo(
    () => JSON.stringify([view, from, to, store.trim(), nonce]), [view, from, to, store, nonce])
  const loading = loadedKey !== key
  const reload = useCallback(() => setNonce(n => n + 1), [])

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const q = new URLSearchParams({ view })
        if (from) q.set('date_from', from)
        if (to) q.set('date_to', to)
        if (store.trim()) q.set('store_code', store.trim())
        const r = await api(`/api/v1/pos/reports/open-balances?${q}`)
        if (!alive) return
        setErr('')
        setData({ ...EMPTY, ...r, summary: { ...EMPTY.summary, ...(r?.summary || {}) } })
      } catch (e: unknown) {
        if (!alive) return
        setErr(String((e as { message?: string })?.message || '') || 'Could not load the balances')
        setData(EMPTY)
      }
      if (alive) setLoadedKey(key)
    })()
    return () => { alive = false }
  }, [key, view, from, to, store])

  const s = data.summary
  const activeView = VIEWS.find(v => v.key === view) || VIEWS[0]

  // THE NOTICE IS THE SERVER'S WORDS, and the migration it names is NOT in the sentence — §19.36:
  // the detail rides PlatformDetail, so only the platform super admin reads it.
  const sentence = useMemo(() => {
    const parts: string[] = []
    if (!data.available) {
      parts.push('Sales on terms are not switched on for this company yet, so there is nothing to '
        + 'collect. This is not an empty list — the register cannot take a sale on account.')
    } else if (s.sales === 0) {
      parts.push('No sale in this range was taken on terms, so nobody owes anything on account. '
        + 'A cash or card sale settles at the till and never appears here.')
    }
    if (s.past_due_count > 0 && s.oldest_days_past_due != null) {
      parts.push(`${s.past_due_count} balance${s.past_due_count === 1 ? '' : 's'} past due, the `
        + `oldest by ${s.oldest_days_past_due} day${s.oldest_days_past_due === 1 ? '' : 's'}.`)
    }
    if (data.collection_note) parts.push(data.collection_note)
    return parts.length ? parts.join(' ') : null
  }, [data, s])

  const notice = sentence == null ? null : (
    <>
      {sentence}
      {!data.available && (
        <PlatformDetail detail="1106_pos_terms_tender" style={{ marginLeft: 6 }} />
      )}
    </>
  )

  return (
    <div style={{ padding: 20, maxWidth: 1400, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>Open balances — sales on terms</h1>
      <p style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 14, maxWidth: 820 }}>
        A sale on terms is money we have earned and not collected. This is every one of them that is
        still open, what it comes to, when it fell due and how late it is.
      </p>

      {/* ── the standard filter bar ───────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10,
        alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          Sold from<DateField value={from} onChange={e => setFrom(e.target.value)} />
        </label>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          to<DateField value={to} onChange={e => setTo(e.target.value)} />
        </label>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          Store
          <input value={store} onChange={e => setStore(e.target.value)} placeholder="All stores"
            style={{ display: 'block', padding: '7px 10px', borderRadius: 8,
              border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }} />
        </label>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          Show
          <select value={view} onChange={e => setView(e.target.value)}
            style={{ display: 'block', padding: '7px 10px', borderRadius: 8,
              border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
            {VIEWS.map(v => <option key={v.key} value={v.key}>{v.label}</option>)}
          </select>
        </label>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={reload}
          disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
      </div>

      {/* ── what is owed ──────────────────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        {[
          { label: 'Open', value: money(s.open_total), tone: 'var(--text)' },
          { label: 'Past due', value: money(s.past_due_total), tone: 'var(--red)' },
          { label: 'Not due yet', value: money(s.not_due_total), tone: 'var(--text)' },
          { label: 'Balances', value: String(s.receivables), tone: 'var(--text)' },
          { label: 'Cleared', value: String(s.cleared), tone: 'var(--green)' },
        ].map(c => (
          <div key={c.label} className="card" style={{ padding: '10px 14px', minWidth: 130 }}>
            <div style={{ fontSize: 10.5, color: 'var(--text3)', textTransform: 'uppercase',
              letterSpacing: '0.06em' }}>{c.label}</div>
            <div style={{ fontSize: 18, fontWeight: 800, color: c.tone }}>{c.value}</div>
          </div>
        ))}
      </div>

      {/* ── the aging ladder, in the server's own bands ───────────────────────────────────────── */}
      {s.past_due_count > 0 && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          {data.bands.map(b => (
            <div key={b} className="card" style={{ padding: '8px 12px', minWidth: 96 }}>
              <div style={{ fontSize: 10.5, color: 'var(--text3)' }}>{b} days</div>
              <div style={{ fontSize: 15, fontWeight: 700 }}>{money(s.bands?.[b] ?? 0)}</div>
            </div>
          ))}
        </div>
      )}

      {err && (
        <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13,
          color: 'var(--red)' }}>{err}</div>
      )}

      <ReportShell
        title="Open balances — sales on terms"
        subtitle={`${activeView.label} · ${data.rows.length} balance${data.rows.length === 1 ? '' : 's'}`
          + `${data.as_of ? ` · as of ${data.as_of}` : ''}`}
        notice={notice}
        noticeTone={s.past_due_count > 0 ? 'warn' : 'info'}
        filename="pos-open-balances"
        columns={[
          { header: 'Sold', field: 'date', type: 'date', role: 'date',
            get: (r: Row) => (r.created_at || '').slice(0, 10) },
          { header: 'Transaction', field: 'transaction_id', get: (r: Row) => r.transaction_id ?? '—' },
          { header: 'Store', field: 'store_code', role: 'store', get: (r: Row) => r.store_code || '—' },
          { header: 'Rep', field: 'employee_name', role: 'rep', get: (r: Row) => r.employee_name || '—' },
          { header: 'Customer', field: 'customer_name', get: (r: Row) => r.customer_name || '—' },
          { header: 'Phone', field: 'customer_phone', get: (r: Row) => r.customer_phone || '—' },
          { header: 'Sale total', field: 'total', type: 'money', money: true, get: (r: Row) => r.total },
          { header: 'Terms', field: 'terms_days',
            get: (r: Row) => (r.terms_days == null ? '—' : `${r.terms_days} days`) },
          { header: 'Due', field: 'due_date', type: 'date', get: (r: Row) => r.due_date || '—' },
          { header: 'Outstanding', field: 'outstanding', type: 'money', money: true,
            get: (r: Row) => r.outstanding },
          // AN AGE WE CANNOT MEASURE SHOWS NO NUMBER. `get` returns null, so an export carries a
          // blank rather than a zero somebody could read as "due today".
          { header: 'Days past due', field: 'days_past_due', type: 'number',
            get: (r: Row) => (r.state === 'past_due' ? r.days_past_due : null) },
          { header: 'Age band', field: 'band', get: (r: Row) => r.band || '—' },
          { header: 'State', field: 'state', get: (r: Row) => STATE_WORDS[r.state] || r.state },
          { header: 'What this is', field: 'note', get: (r: Row) => r.note || '' },
        ]}
        rows={data.rows} compact stickyHeader
      />

      <p style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10, maxWidth: 820 }}>
        {activeView.note} Only sales carrying a tender that settles later appear here — a cash or
        card sale is paid at the till and is never a balance, and a sale with no recorded payment at
        all is not a debt, so neither is counted above.
      </p>
    </div>
  )
}
