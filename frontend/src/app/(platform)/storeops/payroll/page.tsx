'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'

interface PayrollRow {
  employee_id: string; name: string; store: string; pay_rate: number
  scheduled_hours: number; actual_hours: number; shifts: number
  scheduled_pay: number; actual_pay: number
}

export default function PayrollPage() {
  const [month, setMonth] = useState('2026-04')
  const [rows, setRows] = useState<PayrollRow[]>([])
  const [loading, setLoading] = useState(true)

  function load() {
    setLoading(true)
    api(`/api/v1/storeops/payroll?month=${month}`)
      .then(setRows).catch(console.error).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [month])

  const totalScheduled = rows.reduce((s, r) => s + r.scheduled_hours, 0)
  const totalActual    = rows.reduce((s, r) => s + r.actual_hours, 0)
  const totalPayScheduled = rows.reduce((s, r) => s + r.scheduled_pay, 0)
  const totalPayActual    = rows.reduce((s, r) => s + r.actual_pay, 0)

  function exportCSV() {
    const csv = ['Name,Store,Pay Rate,Scheduled Hrs,Actual Hrs,Scheduled Pay,Actual Pay']
    rows.forEach(r => csv.push(`"${r.name}","${r.store}",${r.pay_rate},${r.scheduled_hours.toFixed(1)},${r.actual_hours.toFixed(1)},${r.scheduled_pay.toFixed(2)},${r.actual_pay.toFixed(2)}`))
    const a = document.createElement('a'); a.href = 'data:text/csv,' + encodeURIComponent(csv.join('\n'))
    a.download = `payroll-${month}.csv`; a.click()
  }

  const monthName = new Date(month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Payroll Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {monthName} · {rows.length} employees
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="input" type="month" value={month} onChange={e => setMonth(e.target.value)} style={{ width: 160 }} />
          <button className="btn btn-secondary" onClick={exportCSV}>📥 CSV</button>
        </div>
      </div>

      {/* Summary */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: 'Scheduled Hours', val: totalScheduled.toFixed(1), unit: 'hrs', icon: '📅' },
          { label: 'Actual Hours', val: totalActual.toFixed(1), unit: 'hrs', icon: '⏱️' },
          { label: 'Scheduled Pay', val: fmt(totalPayScheduled), icon: '💵' },
          { label: 'Actual Pay', val: fmt(totalPayActual), icon: '💰' },
        ].map(({ label, val, unit, icon }) => (
          <div key={label} className="card">
            <div style={{ fontSize: 20 }}>{icon}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--accent)', marginTop: 8 }}>
              {val}{unit && <span style={{ fontSize: 13, color: 'var(--text3)', marginLeft: 4 }}>{unit}</span>}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Employee</th>
                <th>Store</th>
                <th style={{ textAlign: 'right' }}>Pay Rate</th>
                <th style={{ textAlign: 'right' }}>Shifts</th>
                <th style={{ textAlign: 'right' }}>Scheduled Hrs</th>
                <th style={{ textAlign: 'right' }}>Actual Hrs</th>
                <th style={{ textAlign: 'right' }}>Variance</th>
                <th style={{ textAlign: 'right' }}>Scheduled Pay</th>
                <th style={{ textAlign: 'right' }}>Actual Pay</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const variance = r.actual_hours - r.scheduled_hours
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 600 }}>{r.name}</td>
                    <td style={{ fontSize: 12, color: 'var(--text3)' }}>{r.store}</td>
                    <td style={{ textAlign: 'right', fontSize: 13 }}>${r.pay_rate.toFixed(2)}/hr</td>
                    <td style={{ textAlign: 'right' }}>{r.shifts}</td>
                    <td style={{ textAlign: 'right' }}>{r.scheduled_hours.toFixed(1)}</td>
                    <td style={{ textAlign: 'right' }}>{r.actual_hours.toFixed(1)}</td>
                    <td style={{ textAlign: 'right', fontWeight: 600, color: variance >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {variance >= 0 ? '+' : ''}{variance.toFixed(1)}
                    </td>
                    <td style={{ textAlign: 'right' }}>{fmt(r.scheduled_pay)}</td>
                    <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.actual_pay)}</td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                <td colSpan={4} style={{ padding: '10px 14px', textAlign: 'right', color: 'var(--text2)' }}>Totals:</td>
                <td style={{ textAlign: 'right', padding: '10px 14px' }}>{totalScheduled.toFixed(1)}</td>
                <td style={{ textAlign: 'right', padding: '10px 14px' }}>{totalActual.toFixed(1)}</td>
                <td style={{ textAlign: 'right', padding: '10px 14px', color: (totalActual - totalScheduled) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {(totalActual - totalScheduled) >= 0 ? '+' : ''}{(totalActual - totalScheduled).toFixed(1)}
                </td>
                <td style={{ textAlign: 'right', padding: '10px 14px' }}>{fmt(totalPayScheduled)}</td>
                <td style={{ textAlign: 'right', padding: '10px 14px', color: 'var(--accent)' }}>{fmt(totalPayActual)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  )
}
