'use client'
import { useEffect, useState } from 'react'
import { api, ORG_ID, fmt } from '@/lib/client'

// In-store coaching panel for the DM store-visit checklist (owner directive 2026-08-18): once the DM
// picks the rep on duty, show THAT rep's current-period KPIs — actual vs target — plus what's costing
// them, so the DM can coach them on the spot. Read-only; reuses the SAME endpoints the Employee
// Dashboard's coaching card uses:
//   1) /core/employee-dashboard  — resolves the roster employee_id to the SALES rep_name the KPI data
//      is keyed on (the short display name "Ali" won't match "ali, mohammad khalid"), plus the period
//      and store, and carries a report_card KPI fallback.
//   2) /commcalc/coaching/{period}?rep=<rep_name>&store=<store>  — per-KPI met/missed with actual vs
//      target, money at risk below full tier, and coaching notes.
// Renders nothing but a compact "no data" line when the rep has no sales/KPI data for the period yet.

type KpiRow = { kpi?: string; label?: string; actual?: number; target?: number; met?: boolean }
type Coach = {
  kpis?: KpiRow[]; tier?: number; at_risk?: number; money_on_table?: number
  short_kpis?: string[]; need_for_full?: number; coaching_notes?: string[]
  chargeback_deducted?: number; chargeback_count?: number
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
// "YYYY-MM-DD" -> "Month YYYY" (the period format commissions are stored/looked-up in). String-parsed
// (no `new Date("YYYY-MM-DD")`) so it never drifts a day across timezones.
function periodOf(isoDate: string): string {
  const m = String(isoDate || '').match(/^(\d{4})-(\d{2})/)
  if (!m) return ''
  return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`
}

export default function VisitCoachingPanel({ employeeId, employeeName, storeCode, visitDate }:
  { employeeId: string; employeeName: string; storeCode?: string; visitDate: string }) {
  const [loading, setLoading] = useState(false)
  const [coach, setCoach] = useState<Coach | null>(null)
  const [fallbackKpis, setFallbackKpis] = useState<KpiRow[] | null>(null)
  const [period, setPeriod] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!employeeId) { setCoach(null); setFallbackKpis(null); setErr(''); return }
    let cancelled = false
    const per = periodOf(visitDate)
    setLoading(true); setErr(''); setCoach(null); setFallbackKpis(null)
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(employeeId)}${per ? `&period=${encodeURIComponent(per)}` : ''}`)
      .then((d: any) => {
        if (cancelled) return
        const repName = d?.employee?.rep_name || d?.employee?.name || employeeName
        const usePeriod = d?.period || per
        const store = d?.employee?.store || storeCode || ''
        setPeriod(usePeriod)
        // report_card.kpi_values is a { key: percent } map — keep it as a last-resort display if the
        // coaching read comes back empty (a rep with a report card but no open coaching row).
        const rc = d?.report_card?.kpi_values
        if (rc && typeof rc === 'object') {
          setFallbackKpis(Object.entries(rc).map(([k, v]) => ({ kpi: k, label: k.toUpperCase(), actual: Number(v), target: undefined })))
        }
        if (!repName || !usePeriod) { setLoading(false); return }
        const qs = `rep=${encodeURIComponent(repName)}${store ? `&store=${encodeURIComponent(store)}` : ''}`
        api(`/api/v1/commcalc/coaching/${encodeURIComponent(usePeriod)}?${qs}`)
          .then((c: any) => { if (!cancelled) setCoach((c?.reps || [])[0] || null) })
          .catch(() => { /* coaching is best-effort; the report_card fallback still renders */ })
          .finally(() => { if (!cancelled) setLoading(false) })
      })
      .catch((e: any) => { if (!cancelled) { setErr(e?.message || String(e)); setLoading(false) } })
    return () => { cancelled = true }
  }, [employeeId, visitDate, employeeName, storeCode])

  const kpis = (coach?.kpis && coach.kpis.length ? coach.kpis : fallbackKpis) || []
  const hasCoach = !!coach

  return (
    <div className="card" style={{ padding: 18, marginBottom: 18, borderTop: '3px solid var(--accent)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>📊 {employeeName} — KPIs this period (actual vs target)</h2>
        <span style={{ fontSize: 12, color: 'var(--text2)' }}>
          {period || '—'}
          {hasCoach && coach!.tier != null ? ` · tier ${Math.round((coach!.tier || 0) * 100)}%` : ''}
        </span>
      </div>

      {loading && <div style={{ display: 'flex', justifyContent: 'center', padding: 24 }}><div className="spinner" /></div>}

      {!loading && err && (
        <div style={{ fontSize: 13, color: 'var(--amber)' }}>Couldn&apos;t load KPIs: {err}</div>
      )}

      {!loading && !err && kpis.length === 0 && (
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
          No KPI data for {employeeName} in {period || 'this period'} yet — nothing to coach against from the numbers.
        </div>
      )}

      {!loading && !err && kpis.length > 0 && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
            {kpis.map((k, i) => {
              const hasTarget = k.target != null
              const met = hasTarget ? !!k.met : undefined
              const bg = met === undefined ? 'var(--surface2)' : met ? '#e6f7ec' : '#fde8e8'
              const fg = met === undefined ? 'var(--text2)' : met ? '#16794a' : '#b42318'
              return (
                <div key={k.kpi || i} style={{ background: bg, borderRadius: 8, padding: '8px 10px', border: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase' }}>{k.label || k.kpi}</div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: fg }}>
                    {k.actual != null ? Number(k.actual).toLocaleString('en-US', { maximumFractionDigits: 1 }) : '—'}
                    {hasTarget && <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text3)' }}> / {Number(k.target).toLocaleString('en-US', { maximumFractionDigits: 1 })}</span>}
                  </div>
                  {met !== undefined && <div style={{ fontSize: 11, fontWeight: 600, color: fg }}>{met ? '✓ on target' : '✗ short'}</div>}
                </div>
              )
            })}
          </div>

          {hasCoach && (
            <div style={{ marginTop: 12, fontSize: 13, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {(coach!.tier ?? 1) < 1 ? (
                <div>💸 <b>{fmt(coach!.at_risk || 0)}</b> at risk — short on <b>{(coach!.short_kpis || []).join(', ') || '—'}</b>
                  {coach!.need_for_full ? <> · hit <b>{coach!.need_for_full}</b> more KPI(s) for full payout</> : null}.</div>
              ) : (
                <div style={{ color: 'var(--green, #16794a)' }}>✅ Full tier — all KPIs on target.</div>
              )}
              {(coach!.chargeback_deducted || 0) > 0 && (
                <div style={{ color: '#b42318' }}>🔻 {fmt(coach!.chargeback_deducted || 0)} chargebacks deducted ({coach!.chargeback_count || 0}).</div>
              )}
              {(coach!.money_on_table || 0) > 0 && (
                <div style={{ color: 'var(--text2)' }}>On the table this period: <b style={{ color: '#b42318' }}>{fmt(coach!.money_on_table || 0)}</b></div>
              )}
              {(coach!.coaching_notes || []).length > 0 && (
                <ul style={{ margin: '4px 0 0', paddingLeft: 16, color: 'var(--text3)', fontSize: 12 }}>
                  {(coach!.coaching_notes || []).map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
