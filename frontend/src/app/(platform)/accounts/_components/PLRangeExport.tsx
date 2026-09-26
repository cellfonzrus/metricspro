'use client'
// The P&L page's MONTH-RANGE export (owner 2026-09-26). Sits next to the single-month export button: pick a
// From and a To month, Prepare, then export Excel / CSV / PDF / Print / Send — one column per month plus a
// Total, for the SAME company / store scope and store / market filter the page is showing. Numbers and
// layout come from `GET /account/pl-range` (see ./plRangeExport.ts); this component only asks for them.
import { useEffect, useMemo, useState } from 'react'
import { api, ORG_ID } from '@/lib/client'
import ReportExportBar from '@/components/ReportExportBar'
import { plRangePayload, plRangeQuery, type PlRangeResponse } from './plRangeExport'

export default function PLRangeExport({ period, periods, scope, scopeLabel, stores, markets }: {
  period: string            // the page's current month (the default To)
  periods: string[]         // the section-wide month list, newest first
  scope: string
  scopeLabel: string
  stores: string[]
  markets: string[]
}) {
  const [open, setOpen] = useState(false)
  const idx = Math.max(0, periods.indexOf(period))
  const [to, setTo] = useState(period)
  const [from, setFrom] = useState(periods[Math.min(idx + 2, periods.length - 1)] || period)   // last 3 months
  const [data, setData] = useState<PlRangeResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const key = `${from}|${to}|${scope}|${stores.join('|')}|${markets.join('|')}`
  useEffect(() => { setData(null); setErr('') }, [key])
  useEffect(() => { if (!open) { setTo(period); setFrom(periods[Math.min(idx + 2, periods.length - 1)] || period) } },
    [period])   // eslint-disable-line react-hooks/exhaustive-deps

  async function prepare() {
    setBusy(true); setErr('')
    try {
      setData(await api(plRangeQuery(from, to, scope, stores, markets, ORG_ID)) as PlRangeResponse)
    } catch (e: any) {
      setErr(String(e?.message || e))
    } finally { setBusy(false) }
  }

  const filtered = stores.length > 0 || markets.length > 0
  const payload = useMemo(() => (data ? plRangePayload(data, scopeLabel || scope, filtered) : null),
    [data, scopeLabel, scope, filtered])
  const monthOpts = [...periods].reverse()          // oldest first reads naturally for a From → To pick

  if (!open) {
    return (
      <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 10px' }} onClick={() => setOpen(true)}
        title="Export the P&L for several months — one column per month plus a Total">
        📅 Month range
      </button>
    )
  }
  return (
    <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', padding: '4px 8px',
      border: '1px solid var(--border)', borderRadius: 8 }}>
      <span style={{ fontSize: 12, color: 'var(--text2)' }}>📅 From</span>
      <select className="select" value={from} onChange={e => setFrom(e.target.value)} aria-label="From month">
        {monthOpts.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      <span style={{ fontSize: 12, color: 'var(--text2)' }}>to</span>
      <select className="select" value={to} onChange={e => setTo(e.target.value)} aria-label="To month">
        {monthOpts.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      {!data && (
        <button className="btn btn-primary" style={{ fontSize: 12, padding: '5px 10px' }} disabled={busy} onClick={prepare}>
          {busy ? '⏳ Preparing…' : 'Prepare export'}
        </button>
      )}
      {data && payload && (
        <>
          <span style={{ fontSize: 12, color: 'var(--text2)' }}>
            {data.months.length} month(s){data.missing_months.length > 0 ? ` · not computed: ${data.missing_months.join(', ')}` : ''}
          </span>
          <ReportExportBar csv title={payload.title} subtitle={payload.subtitle} filename={payload.filename}
            sheets={payload.sheets} />
        </>
      )}
      <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 8px' }} onClick={() => setOpen(false)} aria-label="Close month range">✕</button>
      {err && <div style={{ flexBasis: '100%', fontSize: 12, color: '#dc2626' }}>{err}</div>}
    </div>
  )
}
