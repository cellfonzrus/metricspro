'use client'
import { useState, useEffect } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

// X-Tender Recon — the POS "X report" tenders (commcalc.pos_tender_summary) vs the daily closing sheet
// employees submit (commcalc.daily_closing), per store, cash vs card. Reads GET /commcalc/x-tender-recon.

export default function XTenderReconPage() {
  const [mode, setMode] = useState<'date' | 'period'>('date')
  const [date, setDate] = useState(() => localToday())
  const [period, setPeriod] = useState('')
  const [tolerance, setTolerance] = useState(1)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [storeFilter, setStoreFilter] = useState('')
  const [onlyMismatch, setOnlyMismatch] = useState(false)

  function load() {
    setLoading(true)
    const q = mode === 'date' ? `date=${date}` : `period=${encodeURIComponent(period)}`
    api(`/api/v1/commcalc/x-tender-recon?${q}&tolerance=${tolerance}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [mode, date, period, tolerance])

  const allRows: any[] = data?.rows || []
  const rows = allRows.filter(r =>
    (!storeFilter || (r.store || '').toLowerCase().includes(storeFilter.toLowerCase())) &&
    (!onlyMismatch || !r.match))
  const t = data?.totals || {}
  const scope = mode === 'date' ? date : (period || 'period')

  function buildPayload(): ExportPayload {
    return {
      title: 'X-Tender Recon (POS vs Daily Closing)', subtitle: scope,
      filename: `x-tender-recon_${scope}`.replace(/\s+/g, '-'),
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'POS cash', get: (r: any) => r.pos_cash, money: true },
        { header: 'Closing cash', get: (r: any) => r.closing_cash, money: true },
        { header: 'Cash Δ', get: (r: any) => r.cash_variance, money: true },
        { header: 'POS card', get: (r: any) => r.pos_card, money: true },
        { header: 'Closing card', get: (r: any) => r.closing_card, money: true },
        { header: 'Card Δ', get: (r: any) => r.card_variance, money: true },
        { header: 'POS other', get: (r: any) => r.pos_other, money: true },
        { header: 'Status', get: (r: any) => r.match ? 'OK' : (!r.in_closing ? 'no closing' : !r.in_pos ? 'no X report' : 'MISMATCH') },
      ] }],
    }
  }

  const vColor = (v: number) => Math.abs(v) > tolerance ? '#b91c1c' : 'var(--text3)'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 X-Tender Recon</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            POS <strong>X report</strong> tenders vs the <strong>daily closing sheet</strong> employees submit, per store —
            cash (store + ePay cash) and card (store + ePay credit). A <strong>variance</strong> beyond the tolerance flags a
            cash/card discrepancy to chase.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'date' ? 'var(--accent)' : 'transparent', color: mode === 'date' ? 'white' : 'var(--text2)' }} onClick={() => setMode('date')}>Day</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'period' ? 'var(--accent)' : 'transparent', color: mode === 'period' ? 'white' : 'var(--text2)' }} onClick={() => setMode('period')}>Month</button>
          </div>
          {mode === 'date'
            ? <input className="select" type="date" value={date} onChange={e => setDate(e.target.value)} />
            : <input className="select" placeholder="June 2026" value={period} onChange={e => setPeriod(e.target.value)} style={{ width: 130 }} />}
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>± $
            <input className="select" type="number" value={tolerance} onChange={e => setTolerance(Number(e.target.value) || 0)} style={{ width: 64, marginLeft: 4 }} />
          </label>
          {allRows.length > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : data?.ready === false ? (
        <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{data.note || 'Run migration 062 + import an X report.'}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label="POS cash" value={fmt(t.pos_cash || 0)} sub={`closing ${fmt(t.closing_cash || 0)}`} />
            <Tile label="Cash variance" value={fmt(t.cash_variance || 0)} accent={Math.abs(t.cash_variance || 0) > tolerance ? '#b91c1c' : '#15803d'} />
            <Tile label="POS card" value={fmt(t.pos_card || 0)} sub={`closing ${fmt(t.closing_card || 0)}`} />
            <Tile label="Card variance" value={fmt(t.card_variance || 0)} accent={Math.abs(t.card_variance || 0) > tolerance ? '#b91c1c' : '#15803d'} />
            <Tile label="Stores" value={t.stores ?? 0} />
            <Tile label="Mismatches" value={t.mismatches ?? 0} accent={(t.mismatches || 0) > 0 ? '#b91c1c' : '#15803d'} />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: 13 }}>By store — {scope}</span>
              <input className="select" placeholder="filter store…" value={storeFilter} onChange={e => setStoreFilter(e.target.value)} style={{ width: 180 }} />
              <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={onlyMismatch} onChange={e => setOnlyMismatch(e.target.checked)} /> Mismatches only
              </label>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} shown</span>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>POS cash</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Closing cash</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Cash Δ</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>POS card</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Closing card</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Card Δ</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>POS other</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Status</th>
              </tr></thead>
              <tbody>
                {rows.map((r: any, i: number) => (
                  <tr key={r.store || i} style={{ borderTop: '1px solid var(--border)', background: r.match ? undefined : '#fffafa' }}>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.store}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.pos_cash)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.closing_cash)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600, color: vColor(r.cash_variance) }}>{fmt(r.cash_variance)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.pos_card)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.closing_card)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600, color: vColor(r.card_variance) }}>{fmt(r.card_variance)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text3)' }}>{r.pos_other ? fmt(r.pos_other) : '—'}</td>
                    <td style={{ padding: '7px 12px', fontSize: 12 }}>
                      {r.match ? <span style={{ color: '#15803d' }}>✓ OK</span>
                        : !r.in_closing ? <span style={{ color: '#b45309' }}>no closing sheet</span>
                          : !r.in_pos ? <span style={{ color: '#b45309' }}>no X report</span>
                            : <span style={{ color: '#b91c1c', fontWeight: 600 }}>⚠ mismatch</span>}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tender data for {scope}{onlyMismatch ? ' (no mismatches)' : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, sub, accent }: { label: string; value: any; sub?: string; accent?: string }) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: accent || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}
