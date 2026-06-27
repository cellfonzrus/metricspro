'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID, localToday } from '@/lib/client'
import EmployeeWidgets from '@/components/EmployeeWidgets'

// Manager TEAM snapshot — headline target tiles + per-store + per-rep rollup for the caller's span
// (or a chosen org unit). Shared by the /portal "My Team" tab and the platform /storeops/team page.
// Tap a rep to drill into their full EmployeeWidgets (reuses /core/employee-dashboard).
//
// Auth: pass `token` to scope to the SIGNED-IN manager's span (no unit_id), or pass `unitId` to roll
// up a specific node (admins picking a unit). unitId wins on the backend.

const tile: React.CSSProperties = { flex: '1 1 150px', minWidth: 140, padding: 12, borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '7px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }
const CAT_LABEL: Record<string, string> = { activations: 'Activations', upgrades: 'Upgrades', byod: 'BYOD', accessories: 'Accessories' }
const cap = (s: string) => CAT_LABEL[s] || (s.charAt(0).toUpperCase() + s.slice(1))

export default function TeamSnapshot({ period, token, unitId, today }:
  { period: string; token?: string; unitId?: string; today?: string }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [empMap, setEmpMap] = useState<Record<string, string>>({})   // upper(name) -> employee_id
  const [drillRep, setDrillRep] = useState<string | null>(null)
  const [drill, setDrill] = useState<any>(null)
  const [drillBusy, setDrillBusy] = useState(false)

  const authed = useCallback((path: string) =>
    api(path, token ? { headers: { Authorization: `Bearer ${token}` } } : {}), [token])

  useEffect(() => {
    if (!period) return
    setLoading(true); setErr(''); setDrillRep(null); setDrill(null)
    const qs = `?today=${encodeURIComponent(today || localToday())}${unitId ? `&unit_id=${encodeURIComponent(unitId)}` : ''}`
    authed(`/api/v1/commcalc/team/${encodeURIComponent(period)}/snapshot${qs}`)
      .then(setData).catch((e: any) => setErr(e?.message || 'Failed to load team')).finally(() => setLoading(false))
    api('/api/v1/storeops/employees').then((es: any[]) => {
      const m: Record<string, string> = {}
      ;(es || []).forEach(e => { if (e.employee_id && e.name) m[String(e.name).trim().toUpperCase()] = e.employee_id })
      setEmpMap(m)
    }).catch(() => {})
  }, [period, unitId, today, authed])

  const openRep = (repName: string) => {
    const eid = empMap[String(repName).trim().toUpperCase()]
    if (!eid) { setDrillRep(repName); setDrill({ _noEmp: true }); return }
    setDrillRep(repName); setDrill(null); setDrillBusy(true)
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(eid)}`)
      .then(async (d: any) => {
        const out: any = { dash: d, coach: null, repTargets: null }
        const nm = d?.employee?.name, per = d?.period, store = d?.employee?.store
        if (nm && per) { try { const c = await api(`/api/v1/commcalc/coaching/${encodeURIComponent(per)}?rep=${encodeURIComponent(nm)}`); out.coach = (c?.reps || [])[0] || null } catch {} }
        if (nm && per && store) { try { out.repTargets = await api(`/api/v1/commcalc/targets/${encodeURIComponent(per)}/calendar?scope=rep&store_code=${encodeURIComponent(store)}&rep=${encodeURIComponent(nm)}&today=${localToday()}`) } catch {} }
        setDrill(out)
      }).catch((e: any) => setDrill({ _err: e?.message || 'Failed to load rep' })).finally(() => setDrillBusy(false))
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading team…</div>
  if (err) return <div className="card" style={{ padding: 14, color: '#c0392b', borderColor: '#c0392b' }}>{err}</div>
  if (!data) return null
  if (!data.is_manager && !unitId) {
    return <div className="card" style={{ padding: 18, color: 'var(--text2)', fontSize: 14 }}>
      You don’t manage any team yet. An admin can assign you to an org unit in <b>Org Structure</b>, then your
      stores and reps appear here.
    </div>
  }

  const totals = data.totals || {}
  const catKeys = Object.keys(totals)
  const stores: any[] = data.stores || []
  const reps: any[] = data.reps || []

  return (
    <div>
      {/* headline tiles */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        {catKeys.map(k => {
          const t = totals[k]
          return (
            <div key={k} style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>{cap(k)}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{t.achieved_mtd}<span style={{ fontSize: 13, color: 'var(--text3)', fontWeight: 500 }}> / {t.monthly}</span></div>
              <div style={{ height: 6, borderRadius: 4, background: 'var(--bg2)', marginTop: 6, overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(100, t.pct)}%`, height: '100%', background: t.pct >= 100 ? '#16794a' : t.pct >= 60 ? '#f5a623' : '#dc2626' }} />
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{t.pct}% · need {t.need}</div>
            </div>
          )
        })}
        <div style={{ ...tile, background: data.money_on_table > 0 ? '#fdeaea' : 'var(--surface)' }}>
          <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>Money on table</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: data.money_on_table > 0 ? '#b42318' : 'inherit' }}>${Number(data.money_on_table || 0).toLocaleString()}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>{stores.length} store{stores.length !== 1 ? 's' : ''} · {reps.length} rep{reps.length !== 1 ? 's' : ''}</div>
        </div>
      </div>

      {/* per-store */}
      {stores.length > 0 && (
        <div className="card table-wrapper" style={{ padding: 0, marginBottom: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={th}>Store</th><th style={th}>Conversion</th>
              {catKeys.map(k => <th key={k} style={th}>{cap(k)}</th>)}
            </tr></thead>
            <tbody>
              {stores.map((s, i) => (
                <tr key={i}>
                  <td style={td}>{s.address || s.store_code}</td>
                  <td style={td}>{s.conversion?.rate != null ? `${s.conversion.rate}%` : '—'}</td>
                  {catKeys.map(k => {
                    const c = s.categories?.[k]
                    return <td key={k} style={td}>{c ? `${c.achieved_mtd}/${c.monthly}` : '—'}</td>
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* per-rep */}
      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={th}>Rep</th><th style={th}>Store</th><th style={th}>Tier</th>
            <th style={th}>KPIs</th><th style={th}>Money on table</th><th style={th}></th>
          </tr></thead>
          <tbody>
            {reps.length === 0 && <tr><td style={td} colSpan={6}><span style={{ color: 'var(--text3)' }}>No reps with data for this period.</span></td></tr>}
            {reps.map((r, i) => (
              <tr key={i}>
                <td style={{ ...td, fontWeight: 600 }}>{r.rep}</td>
                <td style={td}>{r.store}</td>
                <td style={td}>{r.tier != null ? `${r.tier}×` : '—'}</td>
                <td style={td}>{r.kpis_met}/{r.total_kpis}</td>
                <td style={{ ...td, color: r.money_on_table > 0 ? '#b42318' : 'inherit' }}>${Number(r.money_on_table || 0).toLocaleString()}</td>
                <td style={td}><button className="btn btn-sm" onClick={() => openRep(r.rep)}>View ▾</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* rep drill-down */}
      {drillRep && (
        <div className="card" style={{ marginTop: 14, padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{drillRep}</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={() => { setDrillRep(null); setDrill(null) }}>Close</button>
          </div>
          {drillBusy && <div style={{ color: 'var(--text3)' }}>Loading…</div>}
          {drill?._noEmp && <div style={{ color: 'var(--text3)', fontSize: 13 }}>No employee record matched “{drillRep}” — ask an admin to link this rep’s Employee ID.</div>}
          {drill?._err && <div style={{ color: '#c0392b', fontSize: 13 }}>{drill._err}</div>}
          {drill?.dash && <EmployeeWidgets data={drill.dash} coach={drill.coach} repTargets={drill.repTargets} />}
        </div>
      )}
    </div>
  )
}
