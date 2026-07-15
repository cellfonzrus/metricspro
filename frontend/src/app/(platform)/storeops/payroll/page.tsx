'use client'
import { useState, useEffect } from 'react'
import { api, fmt, parseLocalDate } from '@/lib/client'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import PtoAccrualPanel from './PtoAccrualPanel'

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

  const monthName = parseLocalDate(month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  // RULE FOUR (§3c): full export set (Excel/PDF/Print/Send) via ReportShell — replaces the old
  // CSV-only button. No PII here (name/store/pay/hours), nothing to mask.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: r => r.name },
    { header: 'Store', field: 'store', role: 'store', get: r => r.store },
    { header: 'Pay Rate', field: 'pay_rate', get: r => `$${Number(r.pay_rate).toFixed(2)}/hr` },
    { header: 'Shifts', field: 'shifts', type: 'number', get: r => r.shifts },
    { header: 'Scheduled Hrs', field: 'scheduled_hours', type: 'number', get: r => r.scheduled_hours.toFixed(1) },
    { header: 'Actual Hrs', field: 'actual_hours', type: 'number', get: r => r.actual_hours.toFixed(1) },
    { header: 'Variance', field: 'variance', type: 'number', get: r => (r.actual_hours - r.scheduled_hours).toFixed(1) },
    { header: 'Scheduled Pay', field: 'scheduled_pay', money: true, get: r => r.scheduled_pay },
    { header: 'Actual Pay', field: 'actual_pay', money: true, get: r => r.actual_pay },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Payroll Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {monthName} · {rows.length} employees
          </p>
        </div>
        <input className="input" type="month" value={month} onChange={e => setMonth(e.target.value)} style={{ width: 160 }} />
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

      <PtoAccrualPanel month={month} />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <ReportShell title="Payroll Report" subtitle={monthName} filename={`payroll-${month}`} columns={cols} rows={rows} />
      )}
    </div>
  )
}
