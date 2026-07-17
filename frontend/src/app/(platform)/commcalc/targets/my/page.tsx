'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID, fmt, fmtN, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

const CATS = [
  { key: 'activations', label: 'Activations', unit: 'count', hint: 'premium + BYOD' },
  { key: 'upgrades', label: 'Upgrades', unit: 'count', hint: 'device upgrades' },
  { key: 'byod', label: 'BYOD', unit: 'count', hint: 'KPI target' },
  { key: 'accessories', label: 'Accessories', unit: 'dollars', hint: 'GP' },
] as const

function val(unit: string, n: number) {
  return unit === 'dollars' ? fmt(n || 0) : fmtN(n || 0, 1)
}

export default function MyTargetsPage() {
  const { period } = usePeriod()
  const [stores, setStores] = useState<any[]>([])
  const [storeCode, setStoreCode] = useState('')
  const [reps, setReps] = useState<string[]>([])
  const [rep, setRep] = useState('')
  const [detail, setDetail] = useState<any>(null)
  const [actionItems, setActionItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  // Load store list once per period (reused from the summary endpoint).
  useEffect(() => {
    api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/summary?org_id=${ORG_ID}`)
      .then(d => {
        setStores(d.stores || [])
        if (d.stores?.length && !storeCode) setStoreCode(d.stores[0].store_code)
      }).catch(console.error)
  }, [period])

  // When a store is picked, pull its rep list (store-scope call returns reps[]).
  useEffect(() => {
    if (!storeCode) return
    setRep(''); setDetail(null)
    api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/calendar?scope=store&store_code=${encodeURIComponent(storeCode)}&org_id=${ORG_ID}`)
      .then(d => setReps(d.reps || [])).catch(console.error)
  }, [storeCode, period])

  useEffect(() => { if (storeCode && rep) loadRep() }, [rep])

  async function loadRep() {
    setLoading(true)
    try {
      const [d, ap] = await Promise.all([
        api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/calendar?scope=rep&store_code=${encodeURIComponent(storeCode)}&rep=${encodeURIComponent(rep)}&org_id=${ORG_ID}&today=${localToday()}`),
        api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/action-plan?store_code=${encodeURIComponent(storeCode)}&rep=${encodeURIComponent(rep)}&org_id=${ORG_ID}&today=${localToday()}`).catch(() => null),
      ])
      setDetail(d)
      const plans = (ap?.stores || []).flatMap((s: any) => s.rep_plans || [])
      const mine = plans.find((p: any) => (p.rep || '').toUpperCase() === rep.toUpperCase())
      setActionItems(mine?.items || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  // Condensed per-rep export (Excel / PDF / Print) — all of a rep's daily targets on one sheet.
  function buildPayload(): ExportPayload {
    const rows: any[] = CATS.map(c => {
      const m = detail.categories?.[c.key] || {}
      return {
        category: c.label, today: val(m.unit, m.today_target), pace: val(m.unit, m.pace),
        need: val(m.unit, m.need), monthly: val(m.unit, m.monthly), achieved: val(m.unit, m.achieved_mtd),
      }
    })
    if (detail.conversion?.rep) {
      rows.push({
        category: 'Conversion (boxes ÷ bill-pay)',
        today: `${detail.conversion.rep.rate}%`, pace: `tgt ${detail.conversion.store.target}%`,
        need: detail.conversion.rep.below_store ? 'below store' : 'OK',
        monthly: `store ${detail.conversion.store.rate}%`,
        achieved: `${detail.conversion.rep.boxes} box / ${detail.conversion.rep.billpays} bp`,
      })
    }
    const cols = [
      { header: 'Target', get: (r: any) => r.category },
      { header: 'Today', get: (r: any) => r.today, align: 'right' as const },
      { header: 'Pace/day', get: (r: any) => r.pace, align: 'right' as const },
      { header: 'Need', get: (r: any) => r.need, align: 'right' as const },
      { header: 'Monthly', get: (r: any) => r.monthly, align: 'right' as const },
      { header: 'Achieved', get: (r: any) => r.achieved, align: 'right' as const },
    ]
    const sheets: any[] = [{ name: 'Daily Targets', columns: cols, rows }]
    if (actionItems.length) {
      sheets.push({
        name: 'Action Plan',
        columns: [
          { header: 'Priority', get: (r: any) => r.severity },
          { header: 'Focus', get: (r: any) => r.title },
          { header: 'Detail', get: (r: any) => r.detail },
        ],
        rows: actionItems,
      })
    }
    return {
      title: `Daily Targets — ${rep}`,
      subtitle: `${storeCode} · ${period} · as of ${detail.today}`,
      filename: `Daily-Targets-${rep.replace(/[^a-z0-9]+/gi, '-')}-${period.replace(/\s+/g, '-')}`,
      sheets,
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>My Targets</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · Your daily goal, pace to finish, and what's left for the month.
        </p>
      </div>

      <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '8px 14px', marginBottom: 18, fontSize: 12, color: '#92400e' }}>
        ⓘ Until logins are enabled, pick your store and name below. This view will be locked to you automatically once sign-in is live.
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <select className="input" value={storeCode} onChange={e => setStoreCode(e.target.value)} style={{ minWidth: 220 }}>
          {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
        </select>
        <select className="input" value={rep} onChange={e => setRep(e.target.value)} style={{ minWidth: 200 }}>
          <option value="">Select your name…</option>
          {reps.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <a href="/commcalc/targets/rep-map" className="btn btn-secondary" style={{ textDecoration: 'none', fontSize: 12 }}>🔗 Merge duplicate reps</a>
      </div>

      {!rep ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
          Select your name to see your targets.
        </div>
      ) : loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
      ) : !detail ? null : (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            <div style={{ fontSize: 13, color: 'var(--text2)' }}>
              <strong>{rep}</strong> · {fmtN(detail.scheduled_hours_total, 0)}h scheduled this month{detail.rep_share != null ? ` · ${Math.round(detail.rep_share * 100)}% of store hours (your target share)` : ''} · today {detail.today}
            </div>
            <><ExportButtons payload={buildPayload} compact /><SendReportButton exportPayload={buildPayload} compact /></>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
            {CATS.map(c => {
              const m = detail.categories?.[c.key]
              if (!m) return null
              return (
                <div key={c.key} className="card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{c.label}</span>
                    <span style={{ fontSize: 11, color: 'var(--text3)' }}>{c.hint}</span>
                  </div>
                  <div style={{ textAlign: 'center', marginBottom: 14 }}>
                    <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Today's Target</div>
                    <div style={{ fontSize: 32, fontWeight: 800, color: 'var(--accent)', lineHeight: 1.1 }}>{val(m.unit, m.today_target)}</div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 7, fontSize: 13, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                    <span style={{ color: 'var(--text2)' }}>Pace to finish /day</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.pace)}</span>
                    <span style={{ color: 'var(--text2)' }}>Need to achieve</span>
                    <span style={{ fontWeight: 600, color: m.need > 0 ? '#b45309' : 'var(--green)' }}>{val(m.unit, m.need)}</span>
                    <span style={{ color: 'var(--text2)' }}>Monthly target</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.monthly)}</span>
                    <span style={{ color: 'var(--text2)' }}>Achieved so far</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.achieved_mtd)}</span>
                    {c.key === 'accessories' && Number(m.setup_fee_mtd || 0) > 0 && (
                      <>
                        <span style={{ color: 'var(--text3)', fontSize: 11 }}>↳ incl. device set-up fee</span>
                        <span style={{ fontWeight: 600, fontSize: 11, color: 'var(--text3)' }}>{fmt(m.setup_fee_mtd)}</span>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {detail.conversion?.rep && (
            <div className="card" style={{ marginTop: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>
                Conversion <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>· boxes ÷ bill-payments · target {detail.conversion.store.target}%</span>
              </div>
              <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
                {[['You', detail.conversion.rep], ['Store', detail.conversion.store]].map(([label, c]: any) => (
                  <div key={label}>
                    <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
                    <div style={{ fontSize: 30, fontWeight: 800, lineHeight: 1.1, color: c.rate >= c.target ? 'var(--green)' : '#dc2626' }}>{c.rate}%</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{c.boxes} boxes / {c.billpays} bill-pays</div>
                  </div>
                ))}
              </div>
              {detail.conversion.rep.below_store && (
                <div style={{ marginTop: 12, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#b91c1c' }}>
                  ⚠️ Your conversion ({detail.conversion.rep.rate}%) is below the store ({detail.conversion.store.rate}%). Convert more bill-pay/walk-in customers into box sales to pull it up.
                </div>
              )}
            </div>
          )}
          {actionItems.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>📋 Action Plan</div>
              <div style={{ display: 'grid', gap: 8 }}>
                {actionItems.map((it, i) => {
                  const color = it.severity === 'critical' ? '#dc2626' : it.severity === 'warning' ? '#d97706' : '#2563eb'
                  return (
                    <div key={i} style={{ borderLeft: `4px solid ${color}`, background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
                      <div style={{ fontWeight: 600, fontSize: 13, color }}>{it.title}</div>
                      <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{it.detail}</div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
