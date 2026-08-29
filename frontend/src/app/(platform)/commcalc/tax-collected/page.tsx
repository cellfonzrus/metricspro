'use client'
import { useState, useEffect, Fragment } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ReportExportBar } from '@/components/ReportExportBar'
import { MultiSelect } from '@/lib/multiselect'

// Tax Collected — per-store drill-down with a date-range + store multi-select + market multi-select.
// Tax is sourced from the UNIFIED sales set (raw_sales ∪ daily_sales_feed deduped by trans_id) on the
// backend, so a feed-only tenant still gets a report. Store/market filters are RULE THREE pickers over the
// values that actually appear in the data (markets come from store_mapping). RULE FOUR exports honor the
// active filters (Excel/PDF via ExportButtons).

type Day = { date: string; tax: number; revenue: number; effective_rate: number }
type Store = { store: string; market: string; tax: number; revenue: number; effective_rate: number; days: Day[] }

export default function TaxCollectedPage() {
  const { period, setPeriod, periods } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})

  useEffect(() => {
    setLoading(true)
    const qs = `org_id=${ORG_ID}&period=${encodeURIComponent(period)}`
      + (start ? `&start=${start}` : '') + (end ? `&end=${end}` : '')
    api(`/api/v1/commcalc/tax-collected?${qs}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }, [period, start, end])

  const allStores: Store[] = data?.stores || []
  const storeOpts = allStores.map(s => ({ value: s.store, label: s.store }))
  const marketOpts = (data?.markets || []).map((m: string) => ({ value: m, label: m }))
  const rows = allStores.filter(s =>
    (!selStores.length || selStores.includes(s.store)) &&
    (!selMarkets.length || selMarkets.includes(s.market || '')))
  const tax = rows.reduce((a, r) => a + (r.tax || 0), 0)
  const revenue = rows.reduce((a, r) => a + (r.revenue || 0), 0)
  const effRate = revenue ? (100 * tax / revenue) : 0
  const rangeLabel = start || end ? `${start || '…'} → ${end || '…'}` : period

  // RULE FOUR §3c: export the CURRENTLY-VISIBLE (filtered) rows to Excel/PDF/Print + Send (email/WhatsApp).
  const dayRows = rows.flatMap(s => s.days.map(d => ({ store: s.store, market: s.market, ...d })))
  const exportSheets = [
    { name: 'By store', rows, columns: [
      { header: 'Store', get: (r: any) => r.store },
      { header: 'Market', get: (r: any) => r.market || '' },
      { header: 'Tax collected', get: (r: any) => r.tax, money: true },
      { header: 'Merchandise (pre-tax)', get: (r: any) => r.revenue, money: true },
      { header: 'Effective rate %', get: (r: any) => r.effective_rate },
    ] },
    { name: 'By store & day', rows: dayRows, columns: [
      { header: 'Store', get: (r: any) => r.store },
      { header: 'Market', get: (r: any) => r.market || '' },
      { header: 'Date', get: (r: any) => r.date },
      { header: 'Tax collected', get: (r: any) => r.tax, money: true },
      { header: 'Merchandise (pre-tax)', get: (r: any) => r.revenue, money: true },
      { header: 'Effective rate %', get: (r: any) => r.effective_rate },
    ] },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Tax Collected</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Retail sales tax collected per store, from the unified sales transactions. Merchandise (ext
            price) is pre-tax; this is the tax on top the customer paid. Drill a store to its daily detail.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={period} onChange={e => setPeriod(e.target.value)}>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {allStores.length > 0 && (
            <ReportExportBar title="Tax Collected"
              subtitle={`${period}${(start || end) ? ` · ${rangeLabel}` : ''}`}
              filename={`tax-collected_${period.replace(/\s+/g, '-')}`} sheets={exportSheets} />
          )}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <>
          {data?.note && <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13, color: '#92400e', background: '#fffbeb', borderLeft: '3px solid #f59e0b' }}>⚠️ {data.note}</div>}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Total tax collected" value={fmt(tax)} color="var(--accent)" />
            <Stat label="Merchandise (pre-tax)" value={fmt(revenue)} />
            <Stat label="Effective tax rate" value={`${effRate.toFixed(2)}%`} color="#16a34a" />
          </div>

          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', gap: 5, alignItems: 'center' }}>
              From <input type="date" className="select" value={start} onChange={e => setStart(e.target.value)} style={{ padding: '4px 6px' }} />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', gap: 5, alignItems: 'center' }}>
              To <input type="date" className="select" value={end} onChange={e => setEnd(e.target.value)} style={{ padding: '4px 6px' }} />
            </label>
            {(start || end) && <button className="btn" style={{ fontSize: 12, padding: '4px 8px' }} onClick={() => { setStart(''); setEnd('') }}>Clear dates</button>}
            <MultiSelect allLabel="All stores" width={170} value={selStores} searchable options={storeOpts} onChange={setSelStores} />
            <MultiSelect allLabel="All markets" width={160} value={selMarkets} searchable options={marketOpts} onChange={setSelMarkets} />
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} store(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tax data for {rangeLabel}.</div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 14px' }}>Store</th>
                  <th style={{ textAlign: 'left', padding: '8px 14px' }}>Market</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Tax collected</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Merchandise (pre-tax)</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Effective rate</th>
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => {
                    const isOpen = !!open[r.store]
                    return (
                      <Fragment key={r.store}>
                        <tr onClick={() => setOpen(o => ({ ...o, [r.store]: !o[r.store] }))}
                            style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : undefined, cursor: 'pointer' }}>
                          <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>
                            <span style={{ color: 'var(--text3)', marginRight: 6 }}>{isOpen ? '▾' : '▸'}</span>{r.store}
                          </td>
                          <td style={{ padding: '9px 14px', fontSize: 13, color: 'var(--text2)' }}>{r.market || '—'}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(r.tax)}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13 }}>{fmt(r.revenue)}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{r.effective_rate}%</td>
                        </tr>
                        {isOpen && r.days.map(d => (
                          <tr key={r.store + d.date} style={{ borderTop: '1px solid var(--border)', background: 'var(--surface)' }}>
                            <td style={{ padding: '6px 14px 6px 34px', fontSize: 12, color: 'var(--text2)' }}>{d.date}</td>
                            <td />
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12 }}>{fmt(d.tax)}</td>
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{fmt(d.revenue)}</td>
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{d.effective_rate}%</td>
                          </tr>
                        ))}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
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
