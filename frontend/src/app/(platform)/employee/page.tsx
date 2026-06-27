'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID, localToday } from '@/lib/client'
import EmployeeWidgets from '@/components/EmployeeWidgets'

const sel: React.CSSProperties = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }

export default function EmployeeDashboardPage() {
  const [emps, setEmps] = useState<any[]>([])
  const [eid, setEid] = useState('')
  const [data, setData] = useState<any>(null)
  const [coach, setCoach] = useState<any>(null)
  const [repTargets, setRepTargets] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api('/api/v1/core/employees').then((d: any) => {
      const list = (d?.employees || []).filter((e: any) => e.employee_id)
      setEmps(list)
      if (list.length && !eid) setEid(list[0].employee_id)
    }).catch(console.error)
  }, [])

  useEffect(() => {
    if (!eid) return
    setLoading(true)
    setCoach(null); setRepTargets(null)
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(eid)}`)
      .then((d: any) => {
        setData(d)
        const nm = d?.employee?.name, per = d?.period, store = d?.employee?.store
        if (nm && per) api(`/api/v1/commcalc/coaching/${encodeURIComponent(per)}?rep=${encodeURIComponent(nm)}`)
          .then((c: any) => setCoach((c?.reps || [])[0] || null)).catch(() => {})
        if (nm && per && store) api(`/api/v1/commcalc/targets/${encodeURIComponent(per)}/calendar?scope=rep&store_code=${encodeURIComponent(store)}&rep=${encodeURIComponent(nm)}&today=${localToday()}`)
          .then(setRepTargets).catch(() => {})
      }).catch(console.error).finally(() => setLoading(false))
  }, [eid])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧑‍💼 Employee Dashboard</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {data?.employee ? `${data.employee.name} · ${data.employee.store || '—'} · ${data.period}` : 'Per-employee performance, schedule, pay, and flags.'}
          </p>
        </div>
        <select style={sel} value={eid} onChange={e => setEid(e.target.value)}>
          {emps.map(e => <option key={e.employee_id} value={e.employee_id}>{e.name}</option>)}
        </select>
      </div>

      {loading || !data ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <EmployeeWidgets data={data} coach={coach} repTargets={repTargets} />
      )}
    </div>
  )
}
