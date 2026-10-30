'use client'
// THE LOG of the cash / credit mismatch (index §POS-14).
//
// Owner 2026-10-30: "create log of the cash or credit mismatch".
//
// WHY THIS SCREEN EXISTS AT ALL, and is not just a warning on the edit. Editing a sale moves what
// the customer OWES; it does not move what was TAKEN. So does voiding one. Either leaves money to
// collect or to refund, and whoever has to act on that is a manager at the end of a shift, not the
// cashier who pressed Save an hour earlier. A warning at the till is necessary and is not enough.
//
// NO ARITHMETIC HERE. Every figure is the server's, from the ONE home that owns the comparison
// (backend/app/modules/pos/sale_settlement.py). This page does not subtract a tender from a total,
// does not decide what counts as a mismatch, and does not word a reason — a second answer to a
// money question on a screen is the duplicate defect the house rules forbid, and this is exactly
// the screen where one would be easy to write.
//
// WHAT IT REFUSES TO SHOW. A sale whose tender was never recorded has NO figure here. 4,603 of the
// 4,605 live sales are imported receipt history with no payment row (measured 2026-10-30), and a
// screen that rendered those as $0.00 differences would be 4,603 accusations. They are counted, and
// named in the notice bar, in the server's own words. "Show what could not be checked" is how
// somebody asks to see them.
//
// RULE FOUR (exports) — it renders through ReportShell, so Excel / PDF / print and 📤 Send by email
// or WhatsApp come for free and respect the ACTIVE filters.
// RULE FIVE (filters) — the standard bar's store / rep / date dimensions all exist for a sale, so
// they are the standard ones; `view` adds the question only this report asks (what to collect, what
// to refund, what could not be checked).
import { useCallback, useEffect, useMemo, useState } from 'react'
import DateField from '@/components/DateField'
import ReportShell from '@/components/ReportShell'
import { api } from '@/lib/client'

type Row = {
  sale_id: string; transaction_id: number | null; created_at: string | null
  store_code: string | null; employee_name: string | null; customer_name: string | null
  status: string | null; revision: number; was_edited: boolean; was_voided: boolean
  total: number | null; balance: number | null; expected: number | null
  tendered: number | null; methods: string; variance: number | null
  verdict: string | null; direction: string | null; measured: boolean
  reason: string | null; mismatch: boolean; attributed_to: string | null
  settled_at: string | null; settled_by: string | null; settled_amount: number | null
}
type Summary = {
  sales: number; settled: number; mismatches: number; not_measurable: number
  to_collect: number; to_refund: number; net_variance: number
  voided_with_tender: number; edited: number
}
type Payload = {
  rows: Row[]; summary: Summary; blockers: { reason: string; count: number }[]
  band: number; log_available: boolean; kind: string
}

const EMPTY: Payload = {
  rows: [], blockers: [], band: 0, log_available: true, kind: 'mismatch',
  summary: { sales: 0, settled: 0, mismatches: 0, not_measurable: 0, to_collect: 0,
             to_refund: 0, net_variance: 0, voided_with_tender: 0, edited: 0 },
}

const VIEWS: { key: string; label: string; note: string }[] = [
  { key: 'mismatch', label: 'Needs attention', note: 'Sales where what was taken and what is owed do not agree.' },
  { key: 'collect', label: 'To collect', note: 'Money the customer still owes.' },
  { key: 'refund', label: 'To refund', note: 'Money that has to go back.' },
  { key: 'unmeasured', label: 'Could not be checked', note: 'Sales with no recorded payment to compare against.' },
  { key: 'all', label: 'Every sale', note: 'Every sale in range, whether it agrees or not.' },
]

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? '—'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)

// Today and thirty days back — the range a manager actually asks for, and short enough that the
// default load is never the whole ledger.
const day = (back: number) =>
  new Date(Date.now() - back * 86400000).toISOString().slice(0, 10)

export default function TenderMismatchPage() {
  const [from, setFrom] = useState(day(30))
  const [to, setTo] = useState(day(0))
  const [store, setStore] = useState('')
  const [view, setView] = useState('mismatch')
  const [data, setData] = useState<Payload>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const q = new URLSearchParams({ kind: view })
      if (from) q.set('date_from', from)
      if (to) q.set('date_to', to)
      if (store.trim()) q.set('store_code', store.trim())
      const r = await api(`/api/v1/pos/reports/tender-mismatch?${q}`)
      setData({ ...EMPTY, ...r, summary: { ...EMPTY.summary, ...(r?.summary || {}) } })
    } catch (e: unknown) {
      setErr(String((e as { message?: string })?.message || '') || 'Could not load the log')
      setData(EMPTY)
    }
    setLoading(false)
  }, [from, to, store, view])

  useEffect(() => { load() }, [load])

  const s = data.summary
  const activeView = VIEWS.find(v => v.key === view) || VIEWS[0]

  // THE NOTICE IS THE SERVER'S WORDS. What could not be measured is said here rather than rendered
  // as rows of zeroes, which is the §72 discipline: a surface that cannot measure something says so.
  const notice = useMemo(() => {
    const parts: string[] = []
    if (!data.log_available) {
      parts.push('Mismatch logging is not switched on (migration 1106 has not been applied), so '
        + 'this screen derives every figure live and no edit has filed what the till saw at the time.')
    }
    for (const b of data.blockers) {
      parts.push(`${b.count} sale${b.count === 1 ? '' : 's'} could not be checked: ${b.reason}.`)
    }
    if (s.voided_with_tender > 0) {
      parts.push(`${s.voided_with_tender} of these are VOIDED sales whose payment is still recorded `
        + '— voiding puts the stock back and does not give the money back.')
    }
    if (data.band > 0) {
      parts.push(`Differences up to ${money(data.band)} are treated as settled, as this organisation declared.`)
    }
    return parts.length ? parts.join(' ') : null
  }, [data, s])

  return (
    <div style={{ padding: 20, maxWidth: 1400, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>Cash / credit mismatch log</h1>
      <p style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 14, maxWidth: 820 }}>
        Editing a sale changes what the customer owes. Voiding one stops them owing it. Neither
        changes the money already taken, so both can leave cash or card to collect or refund. This is
        every one of those, with what it was tendered on.
      </p>

      {/* ── the standard filter bar ───────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10,
        alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          From<DateField value={from} onChange={e => setFrom(e.target.value)} />
        </label>
        <label style={{ fontSize: 11.5, color: 'var(--text3)' }}>
          To<DateField value={to} onChange={e => setTo(e.target.value)} />
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
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={load}
          disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
      </div>

      {/* ── what the range comes to ───────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        {[
          { label: 'To collect', value: money(s.to_collect), tone: 'var(--red)' },
          { label: 'To refund', value: money(s.to_refund), tone: 'var(--amber, #f59e0b)' },
          { label: 'Net', value: money(s.net_variance), tone: 'var(--text)' },
          { label: 'Sales needing attention', value: String(s.mismatches), tone: 'var(--text)' },
          { label: 'Agreed', value: String(s.settled), tone: 'var(--green)' },
          { label: 'Could not be checked', value: String(s.not_measurable), tone: 'var(--text3)' },
        ].map(c => (
          <div key={c.label} className="card" style={{ padding: '10px 14px', minWidth: 140 }}>
            <div style={{ fontSize: 10.5, color: 'var(--text3)', textTransform: 'uppercase',
              letterSpacing: '0.06em' }}>{c.label}</div>
            <div style={{ fontSize: 18, fontWeight: 800, color: c.tone }}>{c.value}</div>
          </div>
        ))}
      </div>

      {err && (
        <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13,
          color: 'var(--red)' }}>{err}</div>
      )}

      <ReportShell
        title="Cash / credit mismatch log"
        subtitle={`${activeView.label} · ${data.rows.length} sale${data.rows.length === 1 ? '' : 's'}`
          + `${from || to ? ` · ${from || '…'} → ${to || '…'}` : ''}`}
        notice={notice}
        noticeTone={s.mismatches > 0 ? 'warn' : 'info'}
        filename="pos-tender-mismatch"
        columns={[
          { header: 'Date', field: 'date', type: 'date', role: 'date',
            get: (r: Row) => (r.created_at || '').slice(0, 10) },
          { header: 'Transaction', field: 'transaction_id', get: (r: Row) => r.transaction_id ?? '—' },
          { header: 'Store', field: 'store_code', role: 'store', get: (r: Row) => r.store_code || '—' },
          { header: 'Rep', field: 'employee_name', role: 'rep', get: (r: Row) => r.employee_name || '—' },
          { header: 'Customer', field: 'customer_name', get: (r: Row) => r.customer_name || '—' },
          { header: 'What happened', field: 'what',
            get: (r: Row) => r.was_voided ? 'Voided' : r.was_edited ? `Edited (rev ${r.revision})` : '—' },
          { header: 'Owed', field: 'expected', type: 'money', money: true,
            get: (r: Row) => r.expected },
          { header: 'Tendered', field: 'tendered', type: 'money', money: true,
            get: (r: Row) => r.tendered },
          { header: 'On', field: 'methods',
            get: (r: Row) => (r.methods || '').replace(/_/g, ' ') || '—' },
          // NOT MEASURED SHOWS NO FIGURE. `get` returns null, so the export carries a blank rather
          // than a zero somebody could total.
          { header: 'Difference', field: 'variance', type: 'money', money: true,
            get: (r: Row) => (r.measured ? r.variance : null) },
          { header: 'Action', field: 'direction',
            get: (r: Row) => !r.measured ? 'Cannot be checked'
              : r.direction === 'collect' ? 'Collect from customer'
              : r.direction === 'refund' ? 'Refund to customer' : 'Nothing owed' },
          { header: 'Against', field: 'attributed_to',
            get: (r: Row) => (r.attributed_to || '—').replace(/_/g, ' ') },
          { header: 'Why not', field: 'reason', get: (r: Row) => r.reason || '' },
          { header: 'Settled', field: 'settled_at', type: 'date',
            get: (r: Row) => (r.settled_at || '').slice(0, 10) },
        ]}
        rows={data.rows} compact stickyHeader
      />

      <p style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10, maxWidth: 820 }}>
        {activeView.note} A difference is only shown where both figures were recorded — a sale with
        no payment on file is counted above and listed under &ldquo;Could not be checked&rdquo;,
        never scored as a shortage.
      </p>
    </div>
  )
}
