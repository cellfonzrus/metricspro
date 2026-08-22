'use client'
import { useState, useEffect, useMemo, Fragment } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'

// Accessory reporting recon: what reps DECLARED as accessory sales on the daily closing sheet vs what
// the sales transactions ACTUALLY show for that store/day. Catches reps entering wrong accessory numbers.
export default function AccessoryReconPage() {
  const [date, setDate] = useState(() => localToday())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): this page had no market
  // dimension at all before — joined here from the org-scoped store roster, same as the other recon
  // pages in this module.
  const [pStores, setPStores] = useState<any[]>([])
  useEffect(() => { apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])

  function load() {
    setLoading(true)
    api(`/api/v1/closing/accessory-recon?date=${date}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [date])

  const allRows: any[] = data?.rows || []
  const marketByCode = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of pStores) if (s.store_code && s.market) m[s.store_code] = s.market
    return m
  }, [pStores])
  const storesForCascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    for (const r of allRows) {
      if (r.store_code && !seen.has(r.store_code)) seen.set(r.store_code, { id: r.store_code, label: r.store_address || r.store_code, market: marketByCode[r.store_code] || null })
    }
    return [...seen.values()]
  }, [allRows, marketByCode])
  const selMarketsFold = useMemo(() => new Set(selMarkets.map(m => m.trim().toLowerCase())), [selMarkets])
  const rows = allRows.filter(r =>
    (!selStores.length || selStores.includes(r.store_code)) &&
    (!selMarketsFold.size || selMarketsFold.has((marketByCode[r.store_code] || '').trim().toLowerCase())) &&
    (!flaggedOnly || r.flag))

  function buildPayload(): ExportPayload {
    return {
      title: 'Accessory Reporting Recon', subtitle: `${date} — declared vs actual`,
      filename: `accessory-recon_${date}`,
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (r: any) => r.store_address },
        { header: 'Market', get: (r: any) => marketByCode[r.store_code] || '' },
        { header: 'Declared', get: (r: any) => r.declared, money: true },
        { header: 'Actual (sales)', get: (r: any) => r.actual, money: true },
        { header: 'Variance', get: (r: any) => r.variance, money: true },
        { header: 'Status', get: (r: any) => r.flag ? r.direction.toUpperCase() : 'OK' },
      ] }],
    }
  }

  const t = data?.totals || {}
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔖 Accessory Reporting Recon</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            What reps <strong>declared</strong> as accessory sales on the closing sheet vs what the
            <strong> sales transactions</strong> actually show, per store, for the day. Accessory isn’t a
            tender — this is a separate tally to catch reps entering wrong accessory numbers.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="select" type="date" value={date} onChange={e => setDate(e.target.value)} />
          {allRows.length > 0 && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Declared (all stores)" value={fmt(t.declared)} />
            <Stat label="Actual (sales)" value={fmt(t.actual)} color="#16a34a" />
            <Stat label="Net variance" value={fmt((t.declared || 0) - (t.actual || 0))} color={Math.abs((t.declared || 0) - (t.actual || 0)) > 1 ? '#dc2626' : '#059669'} />
            <Stat label="Stores flagged" value={`${t.flagged || 0} / ${t.stores || 0}`} color={t.flagged ? '#dc2626' : '#059669'} />
          </div>

          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <MarketStorePicker
              stores={storesForCascade}
              selectedMarkets={selMarkets} onMarketsChange={setSelMarkets}
              selectedStores={selStores} onStoresChange={setSelStores}
            />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={flaggedOnly} onChange={e => setFlaggedOnly(e.target.checked)} /> Discrepancies only
            </label>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} store(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No closing accessory data for {date}.</div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 14px' }}>Store</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Declared</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Actual (sales)</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Variance</th>
                  <th style={{ textAlign: 'center', padding: '8px 14px' }}>Status</th>
                </tr></thead>
                <tbody>
                  {rows.map(r => (
                    <Fragment key={r.store_code}>
                      <tr onClick={() => setOpen(o => ({ ...o, [r.store_code]: !o[r.store_code] }))}
                        style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: r.flag ? '#fffafa' : undefined }}>
                        <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 600 }}>{r.reps?.length ? (open[r.store_code] ? '▾ ' : '▸ ') : ''}{r.store_address}</td>
                        <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13 }}>{fmt(r.declared)}</td>
                        <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13 }}>{fmt(r.actual)}</td>
                        <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, fontWeight: 700, color: r.flag ? '#dc2626' : 'var(--text1)' }}>{r.variance >= 0 ? '+' : ''}{fmt(r.variance)}</td>
                        <td style={{ padding: '9px 14px', textAlign: 'center', fontSize: 12 }}>
                          {r.flag ? <span style={{ background: '#fee2e2', color: '#b91c1c', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>{r.direction === 'over' ? 'OVER‑declared' : 'UNDER‑declared'}</span>
                            : <span style={{ color: '#059669' }}>✓ OK</span>}
                        </td>
                      </tr>
                      {open[r.store_code] && (r.reps || []).map((rep: any, i: number) => (
                        <tr key={r.store_code + '_' + i} style={{ background: 'var(--surface2)', fontSize: 12 }}>
                          <td style={{ padding: '5px 14px 5px 30px', color: 'var(--text2)' }}>{rep.employee_name || '(unnamed)'}</td>
                          <td style={{ padding: '5px 14px', textAlign: 'right' }}>{fmt(rep.acc_sale)}</td>
                          <td colSpan={3} style={{ padding: '5px 14px', color: 'var(--text3)' }}>declared by rep</td>
                        </tr>
                      ))}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12 }}>
            Declared = sum of the reps’ “Accessory Sale” on the closing sheet. Actual = accessory sales
            (ext price on accessory‑classified lines) from the sales transactions for that store/day.
            Click a store to see each rep’s declared figure. Tolerance ±{data?.tolerance ?? 1}.
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
