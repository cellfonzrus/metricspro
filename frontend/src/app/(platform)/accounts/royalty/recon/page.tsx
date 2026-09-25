'use client'
// ROYALTY vs DAILY SALES (owner 2026-09-25, mig 1022, index §37.5): "all sales will be captured via the royalty report
// and reconciled against the daily report uploaded by the tenant". Per center, each royalty SALES line against the
// daily report(s) summed over the month through the line's daily categories (Royalty Report → Line setup), with the
// days and the categories behind the daily figure, the categories no line claims, and a total-level cross-check
// against the register's tenders. The daily side READS the existing POS landings (no second ingest path).
//
// Three states, never a bare $0.00: a line with no daily category and no same-named category says "not mapped";
// no daily rows for the month says what to upload; a measured zero reads as a zero.
import { Fragment, useEffect, useState } from 'react'
import { api, fmt } from '@/lib/client'

const th: React.CSSProperties = { textAlign: 'left', padding: '6px 9px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const thr: React.CSSProperties = { ...th, textAlign: 'right' }
const td: React.CSSProperties = { padding: '6px 9px', fontSize: 13, borderBottom: '1px solid var(--border)', verticalAlign: 'top' }
const tdr: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const STATUS: Record<string, [string, string]> = {
  tie: ['ties', '#15803d'], variance: ['variance', '#b91c1c'], no_daily_map: ['not mapped to a daily category', '#b45309'],
  unknown_line: ['line not in the vocabulary', '#b45309'],
}
// The fields of GET /account/royalty/recon/{period} this page reads.
interface ReconLine {
  line_key: string; label: string; status: string; royalty: number; daily: number; variance: number
  matched_by?: string; days?: Record<string, number>; categories?: Record<string, number>
}
interface ReconCenter {
  report_id: string; center_code: string; period: string; store?: string | null; note?: string
  totals: { royalty_sales: number; daily_total: number; variance: number }
  tenders?: { tenders: number; days: number; variance: number } | null
  lines: ReconLine[]
  unmapped_daily?: { category: string; amount: number; days: Record<string, number>; rows: number }[]
  conflicts?: { category: string; lines: string[] }[]
}
interface ReconData {
  daily_source?: string; daily_rows?: number; match_field?: string; tender_rows?: number
  needs?: string; period?: string; centers?: ReconCenter[]
}
const lastMonth = () => { const d = new Date(); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7) }

export default function RoyaltyReconPage() {
  const [period, setPeriod] = useState(lastMonth())
  const [data, setData] = useState<ReconData | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [err, setErr] = useState('')
  // A load starts on mount and on every month change: `loading` starts true and the month picker flips
  // it (and clears the error) as it changes the month, so the effect below never sets state synchronously.
  const [loading, setLoading] = useState(true)
  const changePeriod = (p: string) => {
    if (p === period) return
    setLoading(true); setErr('')
    setPeriod(p)
  }
  useEffect(() => {
    let alive = true
    api(`/api/v1/account/royalty/recon/${encodeURIComponent(period)}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [period])

  return (
    <div style={{ padding: 16, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Royalty vs Daily Sales</h1>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>Each sales line of the month&apos;s royalty report against the daily report(s) for the same store, summed over the month.</div>
      <label style={{ fontSize: 13 }}>Month <input type="month" value={period} onChange={e => changePeriod(e.target.value)} /></label>
      {loading && <div style={{ marginTop: 10, color: 'var(--text2)' }}>Loading…</div>}
      {err && <div style={{ color: '#b91c1c', marginTop: 10 }}>{err}</div>}
      {data && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>
            Daily source <code>{data.daily_source}</code> · {data.daily_rows} rows · matched on <code>{data.match_field}</code> · {data.tender_rows} register tender rows
          </div>
          {data.needs && <div className="card" style={{ padding: 12, marginBottom: 12, color: '#b45309' }}>{data.needs}</div>}
          {!data.centers?.length && <div className="card" style={{ padding: 12 }}>No royalty report is stored for {data.period}.</div>}
          {data.centers?.map(c => (
            <div key={c.report_id} className="card" style={{ padding: 14, marginBottom: 14 }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>Center {c.center_code} — {c.period}{c.store ? ` · ${c.store}` : ''}</div>
              {c.note && <div style={{ color: '#b45309', fontSize: 13, margin: '4px 0' }}>{c.note}</div>}
              <div style={{ display: 'flex', gap: 18, fontSize: 13, margin: '8px 0', flexWrap: 'wrap' }}>
                <span>Royalty sales <b>{fmt(c.totals.royalty_sales)}</b></span><span>Daily total <b>{fmt(c.totals.daily_total)}</b></span>
                <span>Variance <b style={{ color: c.totals.variance ? '#b91c1c' : undefined }}>{fmt(c.totals.variance)}</b></span>
                {c.tenders ? <span>Register tenders <b>{fmt(c.tenders.tenders)}</b> over {c.tenders.days} day(s) · vs gross sales {fmt(c.tenders.variance)}</span> : <span style={{ color: 'var(--text3)' }}>No register tenders for the month (not measured)</span>}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Line</th><th style={thr}>Royalty</th><th style={thr}>Daily</th><th style={thr}>Variance</th><th style={th}>Status</th><th style={th}></th></tr></thead>
                <tbody>{c.lines.map(l => {
                  const k = c.report_id + l.line_key
                  const [txt, col] = STATUS[l.status] || [l.status, 'inherit']
                  return (
                    <Fragment key={k}>
                      <tr><td style={td}>{l.label}</td><td style={tdr}>{fmt(l.royalty)}</td>
                        <td style={tdr}>{l.status === 'no_daily_map' ? '—' : fmt(l.daily)}</td>
                        <td style={{ ...tdr, color: l.status === 'variance' ? '#b91c1c' : undefined }}>{l.status === 'no_daily_map' ? '—' : fmt(l.variance)}</td>
                        <td style={{ ...td, color: col }}>{txt}{l.matched_by === 'same name' ? ' (by name)' : ''}</td>
                        <td style={td}>{Object.keys(l.days || {}).length > 0 && <button className="btn" onClick={() => setOpen(open === k ? null : k)}>{open === k ? 'Hide' : 'Days'}</button>}</td></tr>
                      {open === k && (
                        <tr><td style={td} colSpan={6}>
                          <div style={{ fontSize: 12.5 }}><b>Categories:</b> {Object.entries(l.categories as Record<string, number>).map(([n, v]) => `${n} ${fmt(v)}`).join(' · ')}</div>
                          <div style={{ fontSize: 12.5 }}><b>Days:</b> {Object.entries(l.days as Record<string, number>).map(([d, v]) => `${d} ${fmt(v)}`).join(' · ')}</div>
                        </td></tr>
                      )}
                    </Fragment>
                  )
                })}</tbody>
              </table>
              {(c.unmapped_daily?.length ?? 0) > 0 && c.unmapped_daily && (
                <div style={{ marginTop: 10, fontSize: 13 }}>
                  <b style={{ color: '#b45309' }}>Daily categories no royalty line claims</b> (add them to a line under Royalty Report → Line setup):
                  {c.unmapped_daily.map(u => <div key={u.category}>{u.category} — {fmt(u.amount)} over {Object.keys(u.days).length} day(s), {u.rows} row(s)</div>)}
                </div>
              )}
              {(c.conflicts?.length ?? 0) > 0 && c.conflicts && <div style={{ marginTop: 8, color: '#b91c1c', fontSize: 13 }}>{c.conflicts.map(x => `'${x.category}' is claimed by ${x.lines.join(' and ')}`).join(' · ')}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
