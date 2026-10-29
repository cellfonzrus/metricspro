'use client'
import { useState, useEffect, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'

// DECLARED vs CAPTURED (mig 1102, index §CLOSING-02). Owner ask 2026-10-29: a rep is paid per
// financing transaction, so what they declare at closing and what the sales feed captured have to
// agree — and when they do not, a manager needs to see which rep, which day and which number is
// missing.
//
// THIS PAGE NAMES NO CATEGORY AND NO PROGRAMME. Every column, label and category comes off
// /closing/declared-vs-captured, which reads the tenant's own declared-metric catalog and its own
// financing vendors. Hard-coding a counterpart here is what harness_metric_catalog_lock.py §G10
// fails the build on.

type Row = {
  date: string; store_code: string; rep: string; rep_key: string
  metric_key: string; label: string; counterpart: string
  units_declared: number; amount_declared: number
  units_captured: number; amount_captured: number
  units_variance: number | null; amount_variance: number | null
  verdict: string; reason: string | null; severity: string
}
type RepRow = {
  store_code: string; rep: string; rep_key: string; days: number
  units_declared: number; units_captured: number
  amount_declared: number; amount_captured: number
  ok: number; gaps: number; pending: number
}
type Resp = {
  configured?: boolean; note?: string
  rows?: Row[]; by_rep?: RepRow[]
  totals?: { rows: number; ok: number; gaps: number; pending: number }
  metrics?: { key: string; label: string; counterpart: string; value_kind: string }[]
  not_reconciled?: string[]
  feed_days?: string[]
  counterpart_status?: Record<string, { status?: string; note?: string }>
  severity_note?: string
  error?: string
}

// The verdicts the server can return, with the words a manager reads. `pending` is deliberately NOT
// styled as a problem with the rep: it is a check we could not run.
const VERDICT: Record<string, { label: string; tone: string }> = {
  ok: { label: 'Agrees', tone: 'var(--ok, #0a7)' },
  variance: { label: 'Numbers differ', tone: 'var(--danger, #c33)' },
  not_captured: { label: 'Declared, not captured', tone: 'var(--danger, #c33)' },
  not_declared: { label: 'Captured, not declared', tone: 'var(--warn, #b70)' },
  pending: { label: 'Could not check', tone: 'var(--text3)' },
}

const cell: React.CSSProperties = {
  padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap',
}
const inp: React.CSSProperties = {
  padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13,
  background: 'var(--surface)',
}

function firstOfMonth(d: string) { return d.slice(0, 8) + '01' }

export default function DeclaredVsCapturedPage() {
  const today = localToday()
  const [from, setFrom] = useState(() => firstOfMonth(today))
  const [to, setTo] = useState(today)
  const [rep, setRep] = useState('')
  const [gapsOnly, setGapsOnly] = useState(true)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  // A filter change re-fetches; the old answer stays visible until the new one lands rather than
  // flashing the page back to "Loading…".

  // The fetch is the external system this effect synchronises with; `loading` is derived from the
  // request rather than set synchronously in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    let live = true
    const q = new URLSearchParams({ date_from: from, date_to: to })
    if (rep) q.set('rep', rep)
    api(`/api/v1/closing/declared-vs-captured?${q.toString()}`)
      .then((d: Resp) => { if (live) { setData(d); setLoading(false) } })
      .catch((e: unknown) => {
        if (live) {
          setData({ error: e instanceof Error ? e.message : String(e) })
          setLoading(false)
        }
      })
    return () => { live = false }
  }, [from, to, rep])

  // A gap is a finding about somebody's day; "could not check" is a finding about OUR data, and the
  // two are deliberately separable so a manager is not sent after a rep over a feed we never loaded.
  const rows = useMemo(() => {
    const all = data?.rows || []
    return gapsOnly ? all.filter(r => r.verdict !== 'ok' && r.verdict !== 'pending') : all
  }, [data, gapsOnly])
  const t = data?.totals || { rows: 0, ok: 0, gaps: 0, pending: 0 }

  function buildPayload(): ExportPayload {
    return {
      title: 'Declared vs captured', subtitle: `${from} to ${to}`,
      filename: `declared-vs-captured_${from}_${to}`,
      sheets: [{ name: 'Findings', rows, columns: [
        { header: 'Date', get: (r: Row) => r.date },
        { header: 'Store', get: (r: Row) => r.store_code },
        { header: 'Employee', get: (r: Row) => r.rep || '(store)' },
        { header: 'Category', get: (r: Row) => r.label },
        { header: 'Declared #', get: (r: Row) => r.units_declared },
        { header: 'Captured #', get: (r: Row) => r.units_captured },
        { header: 'Declared $', get: (r: Row) => r.amount_declared, money: true },
        { header: 'Captured $', get: (r: Row) => r.amount_captured, money: true },
        { header: 'Verdict', get: (r: Row) => VERDICT[r.verdict]?.label || r.verdict },
        { header: 'Why', get: (r: Row) => r.reason || '' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔍 Declared vs Captured</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
          What each employee <strong>declared at closing</strong> against what the{' '}
          <strong>sales feed captured</strong>, per person per store per day. A category appears here
          only if it names something in the feed to be checked against — set that on{' '}
          <Link href="/closing/count-config">Count Fields</Link> under <em>Check against</em>.
        </p>
      </div>

      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>From<br />
          <input style={inp} type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>To<br />
          <input style={inp} type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Employee<br />
          <input style={{ ...inp, width: 200 }} placeholder="any" value={rep}
            onChange={e => setRep(e.target.value)} /></label>
        <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={gapsOnly} onChange={e => setGapsOnly(e.target.checked)} />
          Only the ones that disagree
        </label>
        <div style={{ marginLeft: 'auto' }}><ExportButtons payload={buildPayload} /></div>
      </div>

      {loading && <div className="card" style={{ marginTop: 12 }}>Loading…</div>}
      {!!data?.error && <div className="card" style={{ marginTop: 12 }}>❌ {data.error}</div>}

      {/* NOT CONFIGURED IS SAID, NOT SHOWN AS A CLEAN BILL OF HEALTH. An empty table would read as
          "every declaration agrees", which is the opposite of the truth. */}
      {!loading && data && data.configured === false && (
        <div className="card" style={{ marginTop: 12 }}>
          <strong>Nothing is being checked yet.</strong>
          <p style={{ fontSize: 13, color: 'var(--text2)', margin: '6px 0 0' }}>{data.note}</p>
        </div>
      )}

      {!loading && data?.configured && (<>
        <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
          {[['Agree', t.ok, 'var(--ok, #0a7)'],
            ['Disagree', t.gaps, 'var(--danger, #c33)'],
            ['Could not check', t.pending, 'var(--text3)']].map(([label, n, tone]) => (
            <div key={String(label)} className="card" style={{ minWidth: 150 }}>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>{String(label)}</div>
              <div style={{ fontSize: 24, fontWeight: 700, color: String(tone) }}>{String(n)}</div>
            </div>
          ))}
        </div>

        {/* Why a check could not run — OUR gap, named, so nobody chases a rep over it. */}
        {!!data.counterpart_status && Object.keys(data.counterpart_status).length > 0 && (
          <div className="card" style={{ marginTop: 12 }}>
            <strong style={{ fontSize: 13 }}>Some categories could not be checked</strong>
            <ul style={{ fontSize: 13, color: 'var(--text2)', margin: '6px 0 0', paddingLeft: 18 }}>
              {Object.entries(data.counterpart_status).map(([k, v]) => (
                <li key={k}>{k} — {v?.note || v?.status}</li>
              ))}
            </ul>
          </div>
        )}

        {!!data.by_rep?.length && (
          <div className="card table-wrapper" style={{ marginTop: 12, padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Employee', 'Store', 'Days', 'Declared #', 'Captured #', 'Declared $',
                  'Captured $', 'Agree', 'Disagree', 'Could not check'].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {data.by_rep.map(r => (
                  <tr key={r.store_code + '|' + r.rep_key}>
                    <td style={cell}><strong>{r.rep || '(store-level)'}</strong></td>
                    <td style={cell}>{r.store_code}</td>
                    <td style={cell}>{r.days}</td>
                    <td style={cell}>{r.units_declared}</td>
                    <td style={cell}>{r.units_captured}</td>
                    <td style={cell}>{fmt(r.amount_declared)}</td>
                    <td style={cell}>{fmt(r.amount_captured)}</td>
                    <td style={cell}>{r.ok}</td>
                    <td style={{ ...cell, color: r.gaps ? 'var(--danger, #c33)' : undefined, fontWeight: r.gaps ? 700 : 400 }}>{r.gaps}</td>
                    <td style={{ ...cell, color: 'var(--text3)' }}>{r.pending}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="card table-wrapper" style={{ marginTop: 12, padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Date', 'Store', 'Employee', 'Category', 'Declared #', 'Captured #', 'Declared $',
                'Captured $', 'Verdict', 'Why'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td style={cell}>{r.date}</td>
                  <td style={cell}>{r.store_code}</td>
                  <td style={cell}>{r.rep || '(store-level)'}</td>
                  <td style={cell}>{r.label}</td>
                  <td style={cell}>{r.units_declared}</td>
                  <td style={cell}>{r.units_captured}</td>
                  <td style={cell}>{fmt(r.amount_declared)}</td>
                  <td style={cell}>{fmt(r.amount_captured)}</td>
                  <td style={{ ...cell, color: VERDICT[r.verdict]?.tone, fontWeight: 600 }}>
                    {VERDICT[r.verdict]?.label || r.verdict}
                    {/* A variance we could not measure shows NOTHING, never 0 — the whole point. */}
                    {r.units_variance !== null && r.units_variance !== 0 &&
                      <span style={{ fontWeight: 400, color: 'var(--text2)' }}> ({r.units_variance > 0 ? '+' : ''}{r.units_variance})</span>}
                  </td>
                  <td style={{ ...cell, whiteSpace: 'normal', maxWidth: 380, color: 'var(--text2)', fontSize: 12.5 }}>{r.reason}</td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={10}>
                  {gapsOnly
                    ? 'Nothing disagrees in this range. Untick the filter to see every row, including the checks that could not run.'
                    : 'No declarations and no captures in this range.'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {!!data.not_reconciled?.length && (
          <p style={{ fontSize: 12.5, color: 'var(--text3)', marginTop: 10 }}>
            Not checked against the feed (no counterpart set): {data.not_reconciled.join(', ')}.
          </p>
        )}
        <p style={{ fontSize: 12.5, color: 'var(--text3)', marginTop: 6 }}>{data.severity_note}</p>
      </>)}
    </div>
  )
}
