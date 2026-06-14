'use client'
// Daily Action Plan — prioritized focus areas per store + rep, derived server-side
// from the SAME targets engine + conversion the Daily Targets page uses (so numbers
// reconcile). Store items = per-category catch-up + conversion; rep items = conversion.
import { useState, useEffect } from 'react'
import { api, ORG_ID, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

type Sev = 'critical' | 'warning' | 'good'
interface Item { severity: Sev; metric: string; title: string; detail: string }
interface ConvT { boxes: number; billpays: number; rate: number; target: number; meets_target: boolean }
interface Metric { cat: string; label: string; unit: string; target: number; achieved: number; need: number; pace: number; today_target: number }
interface Kpi { kpi: string; label: string; target: number; actual: number; met: boolean }
interface Commission {
  tier: number; kpis_met: number; total_kpis: number; subtotal: number; total_payout: number
  at_risk: number; short_kpis: string[]; kpis: Kpi[]; t100: number; t75: number
}
interface RepPlan { rep: string; conversion: ConvT; below_store: boolean; items: Item[]; commission: Commission | null }
interface StorePlan {
  store_code: string; address: string; market: string; conversion: ConvT
  metrics: Metric[]; items: Item[]; reps: RepPlan[]; commission_at_risk: number
  counts: { critical: number; warning: number }
}
interface Resp {
  period: string; today: string
  summary: { critical: number; warning: number; stores: number; commission_at_risk: number }
  stores: StorePlan[]
}

const mval = (unit: string, n: number) =>
  unit === 'dollars' ? '$' + Math.round(n || 0).toLocaleString() : String(Math.round((n || 0) * 10) / 10)

const SEV = {
  critical: { bg: '#fef2f2', border: '#fecaca', fg: '#b91c1c', icon: '🔴', label: 'Critical' },
  warning: { bg: '#fffbeb', border: '#fde68a', fg: '#92400e', icon: '🟠', label: 'Warning' },
  good: { bg: '#f0fdf4', border: '#bbf7d0', fg: '#15803d', icon: '🟢', label: 'On track' },
} as const

function tabLink(active: boolean): React.CSSProperties {
  return {
    padding: '7px 16px', borderRadius: 9, fontSize: 13, fontWeight: 600, textDecoration: 'none',
    background: active ? 'var(--accent)' : 'var(--surface2)',
    color: active ? '#fff' : 'var(--text2)', border: '1px solid var(--border)',
  }
}

function ItemRow({ it }: { it: Item }) {
  const c = SEV[it.severity]
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 12px', background: c.bg, border: `1px solid ${c.border}`, borderRadius: 8, marginBottom: 6 }}>
      <span style={{ fontSize: 12 }}>{c.icon}</span>
      <div>
        <span style={{ fontWeight: 700, fontSize: 13, color: c.fg }}>{it.title}</span>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}> — {it.detail}</span>
      </div>
    </div>
  )
}

export default function ActionPlanPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [hideOnTrack, setHideOnTrack] = useState(true)
  const [storeFilter, setStoreFilter] = useState('')
  const [repFilter, setRepFilter] = useState('')

  useEffect(() => { load() }, [period])

  async function load() {
    setLoading(true)
    try {
      const d: Resp = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/action-plan?org_id=${ORG_ID}&today=${localToday()}`)
      setData(d)
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  const keep = (it: Item) => !hideOnTrack || it.severity !== 'good'
  const storeReps = storeFilter
    ? ((data?.stores.find(s => s.store_code === storeFilter)?.reps) || []).map(r => r.rep)
    : []
  const stores = (data?.stores || [])
    .filter(s => !storeFilter || s.store_code === storeFilter)
    .map(s => ({
      ...s,
      items: s.items.filter(keep),
      reps: s.reps
        .filter(r => !repFilter || r.rep === repFilter)
        .map(r => ({ ...r, items: r.items.filter(keep) }))
        .filter(r => (repFilter && r.rep === repFilter) || r.items.length),
    }))
    .filter(s => storeFilter ? true : (s.items.length || s.reps.length))

  function buildPayload(): ExportPayload {
    const rows: any[] = []
    for (const s of stores) {
      const label = s.address || s.store_code
      for (const it of s.items) rows.push({ scope: 'Store', store: label, rep: '', severity: it.severity, focus: it.title, detail: it.detail })
      for (const rp of s.reps) for (const it of rp.items) rows.push({ scope: 'Rep', store: label, rep: rp.rep, severity: it.severity, focus: it.title, detail: it.detail })
    }
    return {
      title: 'Daily Action Plan',
      subtitle: `${period} · as of ${data?.today || ''}`,
      filename: `Action-Plan-${period.replace(/\s+/g, '-')}`,
      sheets: [{
        name: 'Action Items',
        columns: [
          { header: 'Scope', get: (r: any) => r.scope },
          { header: 'Store', get: (r: any) => r.store },
          { header: 'Rep', get: (r: any) => r.rep },
          { header: 'Severity', get: (r: any) => r.severity },
          { header: 'Focus', get: (r: any) => r.focus },
          { header: 'Detail', get: (r: any) => r.detail },
        ],
        rows,
      }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
        <a href="/commcalc/targets" style={tabLink(false)}>Daily Targets</a>
        <a href="/commcalc/targets/action-plan" style={tabLink(true)}>Action Plan</a>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12, marginBottom: 18 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Daily Action Plan</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · auto-generated focus areas per store and rep, ranked by urgency. Same engine as Daily Targets.
          </p>
        </div>
        {data && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={storeFilter} onChange={e => { setStoreFilter(e.target.value); setRepFilter('') }}
              style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
              <option value="">All stores</option>
              {(data.stores || []).map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
            </select>
            <select value={repFilter} onChange={e => setRepFilter(e.target.value)} disabled={!storeFilter}
              style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', opacity: storeFilter ? 1 : 0.5 }}>
              <option value="">All reps</option>
              {storeReps.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', gap: 5, alignItems: 'center' }}>
              <input type="checkbox" checked={hideOnTrack} onChange={e => setHideOnTrack(e.target.checked)} /> Hide on-track
            </label>
            <ExportButtons payload={buildPayload} compact />
            <SendReportButton reportKey="action_plan" filters={{ period, ...(storeFilter ? { store_code: storeFilter } : {}), ...(repFilter ? { rep: repFilter } : {}) }} compact />
          </div>
        )}
      </div>

      {data && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
          <div className="card" style={{ padding: '12px 18px', borderLeft: `4px solid ${SEV.critical.fg}` }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: SEV.critical.fg }}>{data.summary.critical}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>critical items</div>
          </div>
          <div className="card" style={{ padding: '12px 18px', borderLeft: `4px solid ${SEV.warning.fg}` }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: SEV.warning.fg }}>{data.summary.warning}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>warnings</div>
          </div>
          <div className="card" style={{ padding: '12px 18px' }}>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{data.summary.stores}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>stores with targets</div>
          </div>
          <div className="card" style={{ padding: '12px 18px', borderLeft: `4px solid ${data.summary.commission_at_risk > 0 ? '#b45309' : 'var(--green)'}` }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: data.summary.commission_at_risk > 0 ? '#b45309' : 'var(--green)' }}>${Math.round(data.summary.commission_at_risk || 0).toLocaleString()}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>commission at risk</div>
          </div>
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>Loading…</div>
      ) : !data || stores.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          {hideOnTrack ? 'Nothing flagged — every store with targets is on track. 🎉' : 'No targets set. Add monthly targets in Target Settings.'}
        </div>
      ) : (
        stores.map(s => (
          <div key={s.store_code} className="card" style={{ marginBottom: 16, padding: 0, overflow: 'hidden' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', flexWrap: 'wrap', gap: 8 }}>
              <div>
                <span style={{ fontWeight: 700, fontSize: 15 }}>{s.address || s.store_code}</span>
                <span style={{ fontSize: 11, color: 'var(--text3)' }}> · {s.store_code}{s.market ? ` · ${s.market}` : ''}</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {s.counts.critical > 0 && <span style={{ fontSize: 11, fontWeight: 700, color: SEV.critical.fg, background: SEV.critical.bg, border: `1px solid ${SEV.critical.border}`, borderRadius: 20, padding: '2px 10px' }}>{s.counts.critical} critical</span>}
                {s.counts.warning > 0 && <span style={{ fontSize: 11, fontWeight: 700, color: SEV.warning.fg, background: SEV.warning.bg, border: `1px solid ${SEV.warning.border}`, borderRadius: 20, padding: '2px 10px' }}>{s.counts.warning} warning</span>}
                {s.conversion?.billpays > 0 && <span style={{ fontSize: 11, color: 'var(--text2)', alignSelf: 'center' }}>conv {s.conversion.rate}%</span>}
              </div>
            </div>
            <div style={{ padding: '12px 16px' }}>
              {s.metrics?.length > 0 && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                  {s.metrics.map(m => (
                    <div key={m.cat} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px', minWidth: 118 }}>
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{m.label}</div>
                      <div style={{ fontSize: 14, fontWeight: 700 }}>{mval(m.unit, m.achieved)} <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>/ {mval(m.unit, m.target)}</span></div>
                      <div style={{ fontSize: 10, color: m.need > 0 ? '#b45309' : 'var(--green)' }}>{m.need > 0 ? `${mval(m.unit, m.need)} to go · ${mval(m.unit, m.pace)}/day` : 'target met'}</div>
                    </div>
                  ))}
                </div>
              )}
              {s.items.length > 0 && (
                <div style={{ marginBottom: s.reps.length ? 14 : 0 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>Store</div>
                  {s.items.map((it, i) => <ItemRow key={i} it={it} />)}
                </div>
              )}
              {s.reps.map(rp => (
                <div key={rp.rep} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                    {rp.rep}{rp.below_store ? ' · below store' : ''}
                  </div>
                  {rp.items.map((it, i) => <ItemRow key={i} it={it} />)}
                  {rp.commission && (
                    <div style={{ margin: '2px 0 4px' }}>
                      <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>
                        Commission tier {Math.round(rp.commission.tier * 100)}% · {rp.commission.kpis_met}/{rp.commission.total_kpis} KPIs{rp.commission.at_risk > 0 ? ` · $${Math.round(rp.commission.at_risk).toLocaleString()} at risk` : ''}
                      </div>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {rp.commission.kpis.map(k => (
                          <span key={k.kpi} title={`${k.label}: ${k.actual}% vs ${k.target}% target`}
                            style={{
                              fontSize: 10, padding: '2px 7px', borderRadius: 12, fontWeight: 600,
                              background: k.met ? '#f0fdf4' : '#fef2f2', color: k.met ? '#15803d' : '#b91c1c',
                              border: `1px solid ${k.met ? '#bbf7d0' : '#fecaca'}`,
                            }}>
                            {k.met ? '✓' : '✗'} {k.label} {k.actual}%
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
