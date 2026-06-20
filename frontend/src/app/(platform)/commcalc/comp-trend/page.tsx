'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'

export default function CompTrendPage() {
  const [months, setMonths] = useState(6)
  const [storeFilter, setStoreFilter] = useState('')
  const [monthFilter, setMonthFilter] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  function load() {
    setLoading(true)
    api(`/api/v1/commcalc/comp/residual-trend?months=${months}&org_id=${ORG_ID}`)
      .then(setData).catch((e) => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [months])

  const totals = data?.totals_by_month || []
  const allDips = data?.dips || []
  const dips = allDips.filter((d: any) =>
    (!monthFilter || d.period === monthFilter) &&
    (!storeFilter || (d.store || '').toLowerCase().includes(storeFilter.toLowerCase()) ||
                     (d.business_name || '').toLowerCase().includes(storeFilter.toLowerCase())))

  const latest = totals[totals.length - 1] || {}
  const lostThisMonth = dips.filter((d: any) => !monthFilter || d.period === monthFilter)
    .reduce((s: number, d: any) => s + Math.abs(d.delta || 0), 0)
  const vanished = dips.filter((d: any) => d.vanished).length

  function buildPayload(): ExportPayload {
    return {
      title: 'Carrier Residual Trend', subtitle: `last ${months} months${monthFilter ? ` · ${monthFilter}` : ''}${storeFilter ? ` · ${storeFilter}` : ''}`,
      filename: `residual-trend`,
      sheets: [
        { name: 'By month', rows: totals, columns: [
          { header: 'Month', get: (r: any) => r.period },
          { header: 'Residual', get: (r: any) => r.residual, money: true },
          { header: 'Accounts', get: (r: any) => r.accounts },
          { header: 'Δ vs prev', get: (r: any) => r.delta_vs_prev, money: true },
          { header: '% vs prev', get: (r: any) => r.pct_vs_prev },
        ] },
        { name: 'Dips', rows: dips, columns: [
          { header: 'Dipped in', get: (r: any) => r.period },
          { header: 'From month', get: (r: any) => r.prev_period },
          { header: 'Account', get: (r: any) => r.account_id },
          { header: 'Business', get: (r: any) => r.business_name },
          { header: 'Store', get: (r: any) => r.store },
          { header: 'Was', get: (r: any) => r.prev_residual, money: true },
          { header: 'Now', get: (r: any) => r.residual, money: true },
          { header: 'Drop', get: (r: any) => r.delta, money: true },
          { header: '%', get: (r: any) => r.pct },
          { header: 'Reason', get: (r: any) => r.reason },
        ] },
      ],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📉 Carrier Residual Trend</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Month-over-month Comprehensive-Comp residual per account. A <strong>dip</strong> = a residual that fell or an account that
            dropped out of the report (a likely cancellation) — labeled by the month it happened.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Window
            <select className="select" value={months} onChange={e => setMonths(parseInt(e.target.value))} style={{ marginLeft: 6 }}>
              <option value={3}>3 months</option><option value={6}>6 months</option><option value={12}>12 months</option>
            </select>
          </label>
          <input className="select" placeholder="filter store / business…" value={storeFilter} onChange={e => setStoreFilter(e.target.value)} style={{ width: 180 }} />
          {data?.totals_by_month && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : data?.note ? (
        <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{data.note}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label={`Residual — ${latest.period || 'latest'}`} value={fmt(latest.residual || 0)} />
            <Tile label="Δ vs prior month" value={fmt(latest.delta_vs_prev || 0)} accent={(latest.delta_vs_prev || 0) < 0 ? '#b91c1c' : '#15803d'} />
            <Tile label="Active accounts" value={latest.accounts ?? 0} />
            <Tile label={`Residual lost to dips${monthFilter ? '' : ' (all shown)'}`} value={fmt(lostThisMonth)} accent="#b91c1c" />
            <Tile label="Accounts vanished" value={vanished} accent="#b45309" />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>Residual by month — click a month to filter the dips below</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Month</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Residual</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Accounts</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Δ vs prev</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>% vs prev</th>
              </tr></thead>
              <tbody>
                {totals.map((r: any) => (
                  <tr key={r.period} onClick={() => setMonthFilter(monthFilter === r.period ? '' : r.period)}
                      style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: monthFilter === r.period ? 'var(--surface2,#eef2ff)' : undefined }}>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.period}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.residual)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.accounts}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: (r.delta_vs_prev ?? 0) < 0 ? '#b91c1c' : ((r.delta_vs_prev ?? 0) > 0 ? '#15803d' : 'var(--text3)') }}>{r.delta_vs_prev == null ? '—' : fmt(r.delta_vs_prev)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: (r.pct_vs_prev ?? 0) < 0 ? '#b91c1c' : 'var(--text3)' }}>{r.pct_vs_prev == null ? '—' : `${r.pct_vs_prev}%`}</td>
                  </tr>
                ))}
                {totals.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No comp data.</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Residual dips{monthFilter ? ` — ${monthFilter}` : ''} ({dips.length}) — biggest drop first
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Dipped in</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Account</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Business / store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Was</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Now</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Drop</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>%</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Why</th>
              </tr></thead>
              <tbody>
                {dips.map((d: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)', background: d.vanished ? '#fff7ed' : '#fffafa' }}>
                    <td style={{ padding: '6px 12px', fontSize: 12, fontWeight: 600 }}>{d.period}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.account_id || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.business_name || d.store || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(d.prev_residual)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: d.residual === 0 ? '#b91c1c' : undefined }}>{fmt(d.residual)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: '#b91c1c', fontWeight: 600 }}>{fmt(d.delta)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{d.pct}%</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.reason}</td>
                  </tr>
                ))}
                {dips.length === 0 && <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No residual dips{monthFilter ? ` in ${monthFilter}` : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, accent }: { label: string; value: any; accent?: string }) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: accent || 'var(--text)' }}>{value}</div>
    </div>
  )
}
