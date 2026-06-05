'use client'
import { useState, useEffect } from 'react'
import { api, fmtN, ORG_ID } from '@/lib/client'

const KPI_COLS = [
  { key: 'atu_pct',        label: 'ATU %',        target_key: 'kpi_atu_target',        default: 55 },
  { key: 'protect_pct',    label: 'Protect %',    target_key: 'kpi_protect_target',    default: 80 },
  { key: 'byod_pct',       label: 'BYOD %',       target_key: 'kpi_byod_target',       default: 35 },
  { key: 'family_plan_pct',label: 'Family Plan %', target_key: 'kpi_familyplan_target', default: 45 },
  { key: 'tmr3',           label: '3MR %',        target_key: 'kpi_tmr3_target',       default: 70 },
]

export default function KPIPage() {
  const [period] = useState('April 2026')
  const [repData, setRepData] = useState<any[]>([])
  const [cfg, setCfg] = useState<any>({})
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'rep'|'store'>('rep')
  const [marketFilter, setMarketFilter] = useState('')
  const [markets, setMarkets] = useState<string[]>([])

  useEffect(() => {
    Promise.all([
      api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`),
      api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({})),
    ]).then(([comms, config]) => {
      setRepData(comms || [])
      setCfg(config || {})
    }).catch(console.error).finally(() => setLoading(false))
  }, [period])

  function pct(v: number | undefined) {
    return v != null ? (v * 100).toFixed(1) : '—'
  }

  function KPICell({ val, target }: { val: number | undefined; target: number }) {
    if (val == null) return <td style={{ textAlign: 'right', color: 'var(--text3)', padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>—</td>
    const pctVal = val * 100
    const met = pctVal >= target
    return (
      <td style={{ textAlign: 'right', padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
        <span style={{
          fontWeight: 600, fontSize: 13,
          color: met ? 'var(--green)' : pctVal >= target * 0.8 ? 'var(--amber)' : 'var(--red)',
        }}>
          {pctVal.toFixed(1)}%
        </span>
        <div style={{ fontSize: 10, color: 'var(--text3)' }}>target: {target}%</div>
      </td>
    )
  }

  const rows = repData.filter(r => {
    const kv = r.kpi_values || {}
    return Object.keys(kv).length > 0
  })

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>KPI Metrics</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · From DLAR Elevate Go report · {rows.length} reps with KPI data
          </p>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No KPI data — upload DLAR Rep report and run calculation
        </div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Rep</th>
                <th>Store</th>
                <th>Tier</th>
                <th style={{ textAlign: 'right' }}>ATU %</th>
                <th style={{ textAlign: 'right' }}>Protect %</th>
                <th style={{ textAlign: 'right' }}>BYOD %</th>
                <th style={{ textAlign: 'right' }}>Family Plan</th>
                <th style={{ textAlign: 'right' }}>3MR %</th>
                <th style={{ textAlign: 'right' }}>AAL %</th>
                <th style={{ textAlign: 'right' }}>KPIs Met</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const kv = r.kpi_values || {}
                const targets = {
                  atu:        cfg.kpi_atu_target || 55,
                  protect:    cfg.kpi_protect_target || 80,
                  byod:       cfg.kpi_byod_target || 35,
                  familyplan: cfg.kpi_familyplan_target || 45,
                  tmr3:       cfg.kpi_tmr3_target || 70,
                  aal:        cfg.kpi_aal_target || 5,
                }
                const tierPct = Math.round((r.tier || 0) * 100)
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>{r.storeops_name || r.epay_salesperson}</td>
                    <td style={{ color: 'var(--text3)', fontSize: 12 }}>{r.store?.substring(0, 25)}</td>
                    <td>
                      <span className={`badge ${tierPct >= 100 ? 'badge-green' : tierPct >= 75 ? 'badge-amber' : 'badge-red'}`}>
                        {tierPct}%
                      </span>
                    </td>
                    {[
                      { k: 'atu', t: targets.atu },
                      { k: 'protect', t: targets.protect },
                      { k: 'byod', t: targets.byod },
                      { k: 'familyplan', t: targets.familyplan },
                      { k: 'tmr3', t: targets.tmr3 },
                      { k: 'aal', t: targets.aal },
                    ].map(({ k, t }) => (
                      <KPICell key={k} val={kv[k] != null ? kv[k] / 100 : undefined} target={t} />
                    ))}
                    <td style={{ textAlign: 'right', fontWeight: 700, padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
                      <span style={{ color: r.kpis_met >= 7 ? 'var(--green)' : r.kpis_met >= 5 ? 'var(--amber)' : 'var(--red)' }}>
                        {r.kpis_met}/{r.total_kpis}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
