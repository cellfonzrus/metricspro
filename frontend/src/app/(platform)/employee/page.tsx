'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import EmployeeWidgets from '@/components/EmployeeWidgets'
import PortalReports from '@/components/PortalReports'
import MyChargebacks from '@/components/MyChargebacks'
import GoogleReviewsCard from '@/components/GoogleReviewsCard'

const sel: React.CSSProperties = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
// Commissions are stored as "Month YYYY", so build the selector in that format (last ~14 months).
function recentPeriods(n = 14): string[] {
  const out: string[] = []
  const d = new Date(); d.setDate(1)
  for (let i = 0; i < n; i++) { out.push(`${MONTHS[d.getMonth()]} ${d.getFullYear()}`); d.setMonth(d.getMonth() - 1) }
  return out
}

export default function EmployeeDashboardPage() {
  const { user } = useAuth()
  const [emps, setEmps] = useState<any[]>([])
  const [eid, setEid] = useState('')
  const [period, setPeriod] = useState(recentPeriods(1)[0])   // default = current month
  const [data, setData] = useState<any>(null)
  const [coach, setCoach] = useState<any>(null)
  const [repTargets, setRepTargets] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const periods = recentPeriods(14)

  // Scope the picker to the caller's role (self→just them, store→their store, market/DM→their stores,
  // admin→everyone) and DEFAULT to the logged-in employee's own dashboard, not a stranger.
  useEffect(() => {
    api('/api/v1/storeops/employees/visible').then((d: any) => {
      const list = (d?.employees || []).filter((e: any) => e.employee_id)
      setEmps(list)
      const mine = d?.employee_id || user?.employee_id || ''
      setEid(mine && list.some((e: any) => e.employee_id === mine) ? mine : (list[0]?.employee_id || mine))
    }).catch(console.error)
  }, [user?.employee_id])

  useEffect(() => {
    if (!eid) return
    setLoading(true)
    setCoach(null); setRepTargets(null)
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(eid)}${period ? `&period=${encodeURIComponent(period)}` : ''}`)
      .then((d: any) => {
        setData(d)
        // Use the rep name the SALES data uses (e.g. "ali, mohammad khalid") for coaching + targets,
        // not the short employee display name ("Ali") — otherwise they scope to nobody.
        const nm = d?.employee?.rep_name || d?.employee?.name, per = d?.period, store = d?.employee?.store
        if (nm && per) api(`/api/v1/commcalc/coaching/${encodeURIComponent(per)}?rep=${encodeURIComponent(nm)}`)
          .then((c: any) => setCoach((c?.reps || [])[0] || null)).catch(() => {})
        if (nm && per && store) api(`/api/v1/commcalc/targets/${encodeURIComponent(per)}/calendar?scope=rep&store_code=${encodeURIComponent(store)}&rep=${encodeURIComponent(nm)}&today=${localToday()}`)
          .then(setRepTargets).catch(() => {})
      }).catch(console.error).finally(() => setLoading(false))
  }, [eid, period])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧑‍💼 Employee Dashboard</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {data?.employee ? `${data.employee.name} · ${data.employee.store || '—'} · ${data.period}` : 'Per-employee performance, schedule, pay, and flags.'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <select style={sel} value={period} onChange={e => setPeriod(e.target.value)} title="Month">
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {emps.length > 1 && (
            <select style={sel} value={eid} onChange={e => setEid(e.target.value)} title="Employee">
              {emps.map(e => <option key={e.employee_id} value={e.employee_id}>{e.name}{e.home_store ? ` · ${e.home_store}` : ''}</option>)}
            </select>
          )}
        </div>
      </div>

      {loading || !data ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <EmployeeWidgets data={data} coach={coach} repTargets={repTargets} />
          {/* "My Chargebacks" is always the SIGNED-IN caller's own list (identity from token, never
              the picker's selection) — only render it while the picker is on the caller's own
              record, so it never reads like it belongs to whichever employee is being viewed. */}
          {eid && eid === (user?.employee_id || '') && <MyChargebacks />}
          {eid && eid === (user?.employee_id || '') && <GoogleReviewsCard />}
        </>
      )}

      <div style={{ marginTop: 18 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 8px' }}>📊 My Reports</h2>
        <PortalReports compact />
      </div>
    </div>
  )
}
