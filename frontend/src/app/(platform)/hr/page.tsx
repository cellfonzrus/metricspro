'use client'
// HR module — a consolidated, permission-gated VIEW of salary + commission + people data. Everything
// is span-scoped server-side (a manager sees only their area) and the underlying data still lives in
// StoreOps / CommCalc — this is the single place to see total compensation. Editing pay stays on
// StoreOps Admin. Gated by the `hr` module permission (default OFF for managers).
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'

const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
function periodToMonth(p: string): string {
  const parts = (p || '').trim().split(/\s+/)
  if (parts.length === 2) { const mi = MONTHS.indexOf(parts[0].toLowerCase()); if (mi >= 0) return `${parts[1]}-${String(mi + 1).padStart(2, '0')}` }
  if (parts.length === 1 && /^\d{4}-\d{2}/.test(parts[0])) return parts[0].slice(0, 7)
  return ''
}

const th: React.CSSProperties = { textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 12px', fontSize: 13, borderTop: '1px solid var(--border)' }
const tdR: React.CSSProperties = { ...td, textAlign: 'right' }

type Tab = 'comp' | 'employees' | 'payroll' | 'timeoff'

export default function HRPage() {
  const { period } = usePeriod()
  const [tab, setTab] = useState<Tab>('comp')
  const [comp, setComp] = useState<any>(null)
  const [emps, setEmps] = useState<any[]>([])
  const [payroll, setPayroll] = useState<any[]>([])
  const [timeoff, setTimeoff] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      if (tab === 'comp') setComp(await api(`/api/v1/hr/compensation?org_id=${ORG_ID}&period=${encodeURIComponent(period)}`))
      else if (tab === 'employees') setEmps(await api('/api/v1/storeops/employees') || [])
      else if (tab === 'payroll') setPayroll(await api(`/api/v1/storeops/payroll?month=${periodToMonth(period)}`) || [])
      else if (tab === 'timeoff') setTimeoff(await api('/api/v1/storeops/time-off') || [])
    } catch (e: any) { setErr(e?.message || 'Failed to load') }
    setLoading(false)
  }, [tab, period])
  useEffect(() => { load() }, [load])

  function compPayload(): ExportPayload {
    const rows = comp?.rows || []
    return {
      title: 'Total Compensation', subtitle: period, filename: `total-comp-${period.replace(/\s+/g, '-')}`,
      sheets: [{
        name: 'Compensation', columns: [
          { header: 'Employee', get: (r: any) => r.name },
          { header: 'Store', get: (r: any) => r.store || '' },
          { header: 'Pay $/hr', get: (r: any) => r.pay_rate, align: 'right' as const },
          { header: 'Hours', get: (r: any) => r.hours, align: 'right' as const },
          { header: 'Wages', get: (r: any) => r.wages, align: 'right' as const },
          { header: 'Commission', get: (r: any) => r.commission, align: 'right' as const },
          { header: 'Chargebacks', get: (r: any) => r.chargebacks, align: 'right' as const },
          { header: 'Total comp', get: (r: any) => r.total_comp, align: 'right' as const },
        ], rows,
      }],
    }
  }

  const TABS: { k: Tab; label: string }[] = [
    { k: 'comp', label: '💵 Total Compensation' }, { k: 'employees', label: '👥 Employees & Pay' },
    { k: 'payroll', label: '🧾 Payroll' }, { k: 'timeoff', label: '🌴 Time Off' },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧑‍💼 HR</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Salary, payroll and total compensation in one place — scoped to your area. Edit pay on StoreOps Admin.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        {TABS.map(t => (
          <button key={t.k} onClick={() => setTab(t.k)} style={{ padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: tab === t.k ? 'var(--accent)' : 'var(--surface)', color: tab === t.k ? '#fff' : 'var(--text2)' }}>{t.label}</button>
        ))}
        <span style={{ flex: 1 }} />
        {tab === 'comp' && comp?.rows?.length > 0 && <ExportButtons payload={compPayload} compact />}
      </div>

      {err && <div className="card" style={{ padding: 12, color: '#c0392b', borderColor: '#c0392b', marginBottom: 12 }}>{err}</div>}
      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <>
          {tab === 'comp' && (
            <>
              {comp?.totals && (
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                  {[['Wages', comp.totals.wages], ['Commission', comp.totals.commission], ['Chargebacks', -comp.totals.chargebacks], ['Total comp', comp.totals.total_comp]].map(([l, v]: any) => (
                    <div key={l} className="card" style={{ padding: '12px 18px', minWidth: 140 }}>
                      <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>{l}</div>
                      <div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(v)}</div>
                    </div>
                  ))}
                  <div className="card" style={{ padding: '12px 18px', minWidth: 120 }}>
                    <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>People</div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{comp.totals.employees}</div>
                  </div>
                </div>
              )}
              <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    <th style={th}>Employee</th><th style={th}>Store</th>
                    <th style={{ ...th, textAlign: 'right' }}>Pay $/hr</th><th style={{ ...th, textAlign: 'right' }}>Hours</th>
                    <th style={{ ...th, textAlign: 'right' }}>Wages</th><th style={{ ...th, textAlign: 'right' }}>Commission</th>
                    <th style={{ ...th, textAlign: 'right' }}>Chargebacks</th><th style={{ ...th, textAlign: 'right' }}>Total comp</th>
                  </tr></thead>
                  <tbody>
                    {(comp?.rows || []).map((r: any) => (
                      <tr key={r.employee_id || r.name}>
                        <td style={{ ...td, fontWeight: 600 }}>{r.name}</td>
                        <td style={td}>{r.store || '—'}</td>
                        <td style={tdR}>{fmt(r.pay_rate)}</td>
                        <td style={tdR}>{r.hours}</td>
                        <td style={tdR}>{fmt(r.wages)}</td>
                        <td style={tdR}>{fmt(r.commission)}</td>
                        <td style={{ ...tdR, color: r.chargebacks > 0 ? '#b42318' : 'inherit' }}>{r.chargebacks ? '−' + fmt(r.chargebacks) : '—'}</td>
                        <td style={{ ...tdR, fontWeight: 700 }}>{fmt(r.total_comp)}</td>
                      </tr>
                    ))}
                    {(!comp?.rows || comp.rows.length === 0) && <tr><td style={td} colSpan={8}><span style={{ color: 'var(--text3)' }}>No compensation data for {period}.</span></td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {tab === 'employees' && (
            <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Name', 'Emp ID', 'Home store', 'Role', 'Pay $/hr', 'Email', 'Phone'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {emps.map((e: any) => (
                    <tr key={e.id}>
                      <td style={{ ...td, fontWeight: 600 }}>{e.name}</td>
                      <td style={td}>{e.employee_id || '—'}</td>
                      <td style={td}>{e.home_store || '—'}</td>
                      <td style={td}>{e.role || '—'}</td>
                      <td style={td}>{e.pay_rate != null ? fmt(e.pay_rate) : '—'}</td>
                      <td style={td}>{e.email || '—'}</td>
                      <td style={td}>{e.phone || '—'}</td>
                    </tr>
                  ))}
                  {emps.length === 0 && <tr><td style={td} colSpan={7}><span style={{ color: 'var(--text3)' }}>No employees in your area.</span></td></tr>}
                </tbody>
              </table>
            </div>
          )}

          {tab === 'payroll' && (
            <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  <th style={th}>Employee</th><th style={th}>Store</th>
                  <th style={{ ...th, textAlign: 'right' }}>Pay $/hr</th><th style={{ ...th, textAlign: 'right' }}>Sched hrs</th>
                  <th style={{ ...th, textAlign: 'right' }}>Actual hrs</th><th style={{ ...th, textAlign: 'right' }}>Sched pay</th>
                  <th style={{ ...th, textAlign: 'right' }}>Actual pay</th>
                </tr></thead>
                <tbody>
                  {payroll.map((r: any) => (
                    <tr key={r.employee_id || r.name}>
                      <td style={{ ...td, fontWeight: 600 }}>{r.name}</td>
                      <td style={td}>{r.store || '—'}</td>
                      <td style={tdR}>{fmt(r.pay_rate)}</td>
                      <td style={tdR}>{r.scheduled_hours}</td>
                      <td style={tdR}>{r.actual_hours}</td>
                      <td style={tdR}>{fmt(r.scheduled_pay)}</td>
                      <td style={{ ...tdR, fontWeight: 700 }}>{fmt(r.actual_pay)}</td>
                    </tr>
                  ))}
                  {payroll.length === 0 && <tr><td style={td} colSpan={7}><span style={{ color: 'var(--text3)' }}>No payroll rows for {period} (need shifts entered).</span></td></tr>}
                </tbody>
              </table>
            </div>
          )}

          {tab === 'timeoff' && (
            <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Employee', 'Type', 'Start', 'End', 'Status'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {timeoff.map((r: any) => (
                    <tr key={r.id}>
                      <td style={{ ...td, fontWeight: 600 }}>{r.employee_name || r.employee_id}</td>
                      <td style={td}>{r.type || '—'}</td>
                      <td style={td}>{r.start_date}</td>
                      <td style={td}>{r.end_date}</td>
                      <td style={td}><span style={{ fontSize: 12, fontWeight: 600, color: r.status === 'approved' ? '#15803d' : r.status === 'denied' ? '#b42318' : '#b45309' }}>{r.status}</span></td>
                    </tr>
                  ))}
                  {timeoff.length === 0 && <tr><td style={td} colSpan={5}><span style={{ color: 'var(--text3)' }}>No time-off requests in your area.</span></td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
