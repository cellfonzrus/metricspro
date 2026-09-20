'use client'
// BILL PAYMENTS — the bill payments EXTRACTED from the sales export (owner 2026-09-20: "otherwise bill
// payments should be extracted from their sales reports and then assigned a separate report for
// themselves"), beside the carrier's own bill-pay report when one is present for the period.
//
// DISPLAY ONLY. This page reads GET /commcalc/billpay-extract/{period} and renders it. The judgement —
// which sale line IS a bill payment — is the ONE rule every bill-payment surface already rides
// (the org's `bill_payment` metric definition: exact department / category membership, substring on
// the product description), applied server-side by the same predicate the Executive MTD columns use.
// Nothing here widens it: a token that matched nothing is LISTED so the person can adjust the
// definition where it lives (Executive MTD → Metric definitions). No carrier or processor is named
// in this file (RULE TWO); the feed side says which processor the coverage recon read.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

type Line = {
  trans_id: string; trans_date: string; store: string; store_key: string; salesperson: string
  department: string; category: string; product_desc: string; tender_type: string | null
  amount: number; matched_by: string; matched_token: string | null
}
type DayRow = {
  store: string; date: string; sales_amount: number | null; sales_count: number | null
  feed_amount: number | null; difference: number | null; status: string
}
type Payload = {
  period: string; store: string | null
  rule: { bucket: string; rules: Record<string, string[]>; source: string | null; edit: string; note: string }
  basis: { sales_rows: number; rows_scanned: number; rows_skipped: number; rows_unmatched: number; feed_processor: string | null; feed_present: boolean; feed_reader: string }
  totals: { lines: number; sum: number; sum_feed: number | null; difference: number | null; days_compared: number; days_sales_only: number; days_feed_only: number }
  per_store_day: DayRow[]; per_store: { store: string; amount: number; count: number }[]
  per_token: { by: string; token: string | null; amount: number; count: number }[]
  token_coverage: { matched: { by: string; token: string; count: number }[]; unmatched_tokens: { by: string; token: string; count: number }[] }
  lines: Line[]; truncated: boolean
}

const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `$${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const num = (n: number | null | undefined) => (n === null || n === undefined ? '—' : Number(n).toLocaleString())

function defaultPeriod() {
  const d = new Date()
  return d.toLocaleString('en-US', { month: 'long', year: 'numeric' })
}

export default function BillPaymentsPage() {
  const [period, setPeriod] = useState(defaultPeriod())
  const [store, setStore] = useState('')
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [tab, setTab] = useState<'days' | 'lines' | 'tokens'>('days')

  // The fetch lives in the effect; every setState happens in an async callback (the loading flag is
  // raised by the handlers that change the query, never synchronously inside the effect body).
  useEffect(() => {
    let alive = true
    api(`/api/v1/commcalc/billpay-extract/${encodeURIComponent(period)}${store ? `?store=${encodeURIComponent(store)}` : ''}`)
      .then((r: Payload) => { if (alive) { setData(r); setErr('') } })
      // NEVER swallow: an error rendered as "no bill payments" is the worst lie this page could tell.
      .catch((e: unknown) => { if (alive) setErr((e instanceof Error && e.message) || 'Could not load the report.') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [period, store, tick])

  const t = data?.totals
  const b = data?.basis
  return (
    <div style={{ maxWidth: 1150, margin: '0 auto', padding: '4px 0 32px' }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: '0 0 4px' }}>Bill Payments</h1>
      <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 12 }}>
        Bill payments <b>extracted from your sales export</b> by your bill-payment definition — every line, with the token that
        matched it — and, when your carrier&apos;s own bill-payment report is loaded for the period, both figures and the difference
        per store-day. This page reports; it books nothing.
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <input value={period} onChange={e => { setLoading(true); setPeriod(e.target.value) }} placeholder="Period, e.g. August 2026" style={inp} />
        <input value={store} onChange={e => { setLoading(true); setStore(e.target.value) }} placeholder="Store (optional)" style={inp} />
        <button className="btn btn-secondary" onClick={() => { setLoading(true); setTick(x => x + 1) }}>Refresh</button>
      </div>

      {loading && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Loading…</div>}
      {!!err && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 10, padding: 14, fontSize: 13, color: '#991b1b' }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>❌ {err}</div>
          <div style={{ marginBottom: 10 }}>The page could not reach the server — this does <b>not</b> mean there are no bill payments.</div>
          <button className="btn btn-secondary" onClick={() => { setLoading(true); setErr(''); setTick(x => x + 1) }}>Retry</button>
        </div>
      )}

      {!loading && !err && data && t && b && (<>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12, marginBottom: 14 }}>
          <Tile label="Bill-pay lines in the sales export" value={num(t.lines)} tone="info" />
          <Tile label="Σ extracted" value={money(t.sum)} tone="info" />
          <Tile label={b.feed_present ? `Σ carrier report (${b.feed_processor})` : 'Carrier bill-pay report'} value={b.feed_present ? money(t.sum_feed) : 'not loaded'} tone={b.feed_present ? 'ok' : 'warn'} />
          <Tile label="Difference (days both carry)" value={b.feed_present ? `${money(t.difference)} · ${num(t.days_compared)} day(s)` : '—'} tone={b.feed_present && t.difference !== 0 ? 'warn' : 'ok'} />
        </div>

        {/* THE BASIS — what was scanned, by which rule, from where */}
        <div style={{ background: 'var(--card,#fff)', border: '1px solid var(--border)', borderRadius: 10, padding: 12, fontSize: 12.5, marginBottom: 14, lineHeight: 1.6 }}>
          <div><b>Rule:</b> your <code>bill_payment</code> definition ({data.rule.source || 'built-in'}) — {Object.entries(data.rule.rules || {}).map(([k, v]) => `${k}: ${(v || []).join(', ')}`).join(' · ') || 'no tokens'}. {data.rule.note}.</div>
          <div><b>Scanned:</b> {num(b.sales_rows)} sales rows for {data.period}{data.store ? ` · store ${data.store}` : ''} — {num(b.rows_scanned)} countable (voided / returns / no rep skipped: {num(b.rows_skipped)}), {num(b.rows_unmatched)} not bill payments.</div>
          <div><b>Carrier side:</b> {b.feed_present ? <>the processor bill-pay feed read by the coverage recon&apos;s own reader ({b.feed_reader}).</> : <>no processor bill-pay feed carries this period{b.feed_processor ? ` (processor ${b.feed_processor})` : ' — no bill-pay processor is configured'}; the extraction above IS the bill-payments figure. Upload the carrier&apos;s bill-payment report on the onboarding intake to compare.</>}</div>
          <div><b>Adjust the definition:</b> <a href="/commcalc/exec/mtd">Executive MTD → Metric definitions → Bill Payment</a> ({data.rule.edit}).</div>
        </div>

        {data.token_coverage.unmatched_tokens.length > 0 && (
          <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: 12, fontSize: 12.5, color: '#92400e', marginBottom: 14 }}>
            <b>Tokens in your definition that matched nothing this period:</b> {data.token_coverage.unmatched_tokens.map(u => `${u.by} "${u.token}"`).join(', ')}.
            A definition describing nobody&apos;s data is how a bill-payment column reads a quiet zero — check the spelling against the department / category values that do occur.
          </div>
        )}

        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          {(['days', 'lines', 'tokens'] as const).map(k => (
            <button key={k} className={`btn ${tab === k ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab(k)}>
              {k === 'days' ? `Per store-day (${data.per_store_day.length})` : k === 'lines' ? `Lines (${num(t.lines)})` : 'What matched'}
            </button>
          ))}
        </div>

        {tab === 'days' && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                <th style={th}>Store</th><th style={th}>Day</th><th style={{ ...th, textAlign: 'right' }}>From sales</th><th style={{ ...th, textAlign: 'right' }}>Lines</th>
                <th style={{ ...th, textAlign: 'right' }}>Carrier report</th><th style={{ ...th, textAlign: 'right' }}>Difference</th><th style={th}>Status</th>
              </tr></thead>
              <tbody>
                {data.per_store_day.length === 0 && <tr><td colSpan={7} style={{ ...td, color: 'var(--text3)' }}>No bill-payment line matched in this period.</td></tr>}
                {data.per_store_day.map(r => (
                  <tr key={`${r.store}|${r.date}`} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}>{r.store}</td><td style={td}>{r.date}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{money(r.sales_amount)}</td><td style={{ ...td, textAlign: 'right' }}>{num(r.sales_count)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{money(r.feed_amount)}</td>
                    <td style={{ ...td, textAlign: 'right', color: r.difference === null ? 'inherit' : r.difference === 0 ? '#15803d' : '#b45309' }}>{r.difference === null ? '—' : money(r.difference)}</td>
                    <td style={td}>{STATUS[r.status] || r.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'lines' && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
              <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                <th style={th}>Date</th><th style={th}>Store</th><th style={th}>Transaction</th><th style={th}>Rep</th><th style={th}>Department</th><th style={th}>Category</th><th style={th}>Product</th><th style={th}>Tender</th><th style={{ ...th, textAlign: 'right' }}>Amount</th><th style={th}>Matched by</th>
              </tr></thead>
              <tbody>
                {data.lines.map((l, i) => (
                  <tr key={`${l.trans_id}|${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}>{l.trans_date}</td><td style={td}>{l.store}</td><td style={{ ...td, fontFamily: 'ui-monospace, monospace' }}>{l.trans_id}</td><td style={td}>{l.salesperson}</td>
                    <td style={td}>{l.department}</td><td style={td}>{l.category}</td><td style={td}>{l.product_desc}</td><td style={td}>{l.tender_type || '—'}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{money(l.amount)}</td>
                    <td style={td}><span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, background: 'rgba(37,99,235,.12)', color: '#1d4ed8' }}>{l.matched_by}: {l.matched_token}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.truncated && <div style={{ fontSize: 12.5, color: 'var(--text3)', marginTop: 8 }}>Showing the first {num(data.lines.length)} lines; narrow by store to see the rest.</div>}
          </div>
        )}

        {tab === 'tokens' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>By matched token</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}><th style={th}>Rule</th><th style={th}>Token</th><th style={{ ...th, textAlign: 'right' }}>Lines</th><th style={{ ...th, textAlign: 'right' }}>Σ</th></tr></thead>
                <tbody>{data.per_token.map(p => <tr key={`${p.by}|${p.token}`} style={{ borderTop: '1px solid var(--border)' }}><td style={td}>{p.by}</td><td style={td}>{p.token}</td><td style={{ ...td, textAlign: 'right' }}>{num(p.count)}</td><td style={{ ...td, textAlign: 'right' }}>{money(p.amount)}</td></tr>)}</tbody>
              </table>
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>By store</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)' }}><th style={th}>Store</th><th style={{ ...th, textAlign: 'right' }}>Lines</th><th style={{ ...th, textAlign: 'right' }}>Σ</th></tr></thead>
                <tbody>{data.per_store.map(p => <tr key={p.store} style={{ borderTop: '1px solid var(--border)' }}><td style={td}>{p.store}</td><td style={{ ...td, textAlign: 'right' }}>{num(p.count)}</td><td style={{ ...td, textAlign: 'right' }}>{money(p.amount)}</td></tr>)}</tbody>
              </table>
            </div>
          </div>
        )}
      </>)}
    </div>
  )
}

const STATUS: Record<string, string> = {
  match: '✅ matches the carrier report', differs: '⚠ differs', sales_only: 'sales export only', feed_only: 'carrier report only',
}

function Tile({ label, value, tone }: { label: string; value: string; tone: 'ok' | 'warn' | 'info' }) {
  const c = tone === 'warn' ? { bg: '#fffbeb', bd: '#fde68a', fg: '#92400e' }
          : tone === 'info' ? { bg: '#eff6ff', bd: '#bfdbfe', fg: '#1e40af' }
          : { bg: 'var(--card, #fff)', bd: 'var(--border)', fg: 'var(--text2)' }
  return (
    <div style={{ background: c.bg, border: `1px solid ${c.bd}`, borderRadius: 10, padding: '12px 14px' }}>
      <div style={{ fontSize: 11.5, color: c.fg, opacity: 0.85 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color: c.fg, marginTop: 2 }}>{value}</div>
    </div>
  )
}

const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }
const th: React.CSSProperties = { padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', verticalAlign: 'top' }
