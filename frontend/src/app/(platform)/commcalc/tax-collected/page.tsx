'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { MultiSelect } from '@/lib/multiselect'

export default function TaxCollectedPage() {
  const { period, setPeriod, periods } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selStores, setSelStores] = useState<string[]>([])

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/tax-collected?org_id=${ORG_ID}&period=${encodeURIComponent(period)}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }, [period])

  const allStores: any[] = data?.stores || []
  const storeOpts = allStores.map(s => ({ value: s.store, label: s.store }))
  const rows = allStores.filter(s => !selStores.length || selStores.includes(s.store))
  const t = data?.totals || {}
  const effRate = t.revenue ? (100 * (t.tax || 0) / t.revenue) : 0

  function buildPayload(): ExportPayload {
    return {
      title: 'Tax Collected', subtitle: period,
      filename: `tax-collected_${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Tax collected', get: (r: any) => r.tax, money: true },
        { header: 'Merchandise (pre-tax)', get: (r: any) => r.revenue, money: true },
        { header: 'Effective rate %', get: (r: any) => r.effective_rate },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Tax Collected</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Retail sales tax collected per store, from the sales transactions. Merchandise (ext price) is
            pre-tax; this is the tax on top that the customer paid and that's included in the tenders.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={period} onChange={e => setPeriod(e.target.value)}>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {allStores.length > 0 && <ExportButtons payload={buildPayload} />}
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
            <Stat label="Total tax collected" value={fmt(t.tax)} color="var(--accent)" />
            <Stat label="Merchandise (pre-tax)" value={fmt(t.revenue)} />
            <Stat label="Effective tax rate" value={`${effRate.toFixed(2)}%`} color="#16a34a" />
          </div>

          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <MultiSelect allLabel="All stores" width={170} value={selStores} searchable options={storeOpts} onChange={setSelStores} />
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} store(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tax data for {period}.</div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 14px' }}>Store</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Tax collected</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Merchandise (pre-tax)</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Effective rate</th>
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.store} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : undefined }}>
                      <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{r.store}</td>
                      <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(r.tax)}</td>
                      <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13 }}>{fmt(r.revenue)}</td>
                      <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{r.effective_rate}%</td>
                    </tr>
                  ))}
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
