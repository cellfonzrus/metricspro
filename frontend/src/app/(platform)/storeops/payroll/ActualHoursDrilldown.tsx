'use client'
// Deliverable 2 (owner directive 2026-07-27): clicking a rep's Actual Hrs row on the Payroll Report
// / Hours & Payroll pages opens this — the day-by-day composition behind that number: shift vs.
// punch vs. manual-hours line items, which were manually edited (Payroll Change Log, migration 414),
// and day subtotals that reconcile EXACTLY to the total shown on the report row (including a plain
// `double_counted` flag when the underlying data genuinely counts a day twice — see Deliverable 3 —
// rather than silently hiding it).
import { useEffect, useState } from 'react'
import { api, fmt, fmtN } from '@/lib/client'

const BUSINESS_TZ = 'America/New_York'
const fmtTime = (t: string | null) => {
  if (!t) return '—'
  try { return new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: BUSINESS_TZ }) }
  catch { return t }
}

export default function ActualHoursDrilldown({ employeeId, name, start, end, onClose }: {
  employeeId: string; name?: string; start: string; end: string; onClose: () => void
}) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api(`/api/v1/storeops/payroll/actual-hours-detail?employee_id=${encodeURIComponent(employeeId)}&start=${start}&end=${end}`)
      .then((r: any) => { if (!cancelled) setData(r) })
      .catch((e: any) => { if (!cancelled) setErr(e?.message || 'Failed to load') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [employeeId, start, end])

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ maxWidth: 820, width: '100%', maxHeight: '85vh', overflow: 'auto', padding: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18 }}>⏱️ Actual hours — {data?.name || name || employeeId}</h2>
            <p style={{ margin: '2px 0 0', fontSize: 13, color: 'var(--text2)' }}>{start} → {end}</p>
          </div>
          <button className="btn" onClick={onClose}>✕ Close</button>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><div className="spinner" /></div>
        ) : err ? (
          <div style={{ color: '#dc2626', fontSize: 13 }}>{err}</div>
        ) : !data || !data.days?.length ? (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
            No shifts or punches in range.
            {data?.pay_basis && data.pay_basis !== 'hourly' && (
              <div style={{ marginTop: 10, fontSize: 12 }}>
                💰 Salaried ({data.pay_basis}) — pay is not hours-derived.
                {data.salary_derived_pay != null
                  ? <> This period's pay: <strong>{fmt(data.salary_derived_pay)}</strong>{data.salary_prorated ? ' (prorated for this range)' : ''}.</>
                  : (data.salary_note ? ` ${data.salary_note}` : '')}
              </div>
            )}
          </div>
        ) : (
          <>
            {/* Salary pay-basis (owner directive 2026-07-27, Deliverable 3): "salaried — pay not
                hours-derived" note — these hours are shown for reference (schedule/attendance
                composition), the AUTHORITATIVE pay figure is GET /payroll's own derived salary row. */}
            {data.pay_basis && data.pay_basis !== 'hourly' && (
              <div className="card" style={{ marginBottom: 12, padding: '10px 14px', fontSize: 12, background: 'var(--surface2)' }}>
                💰 Salaried ({data.pay_basis}) — pay is <strong>not</strong> derived from these hours.
                {data.salary_derived_pay != null
                  ? <> This period's pay: <strong>{fmt(data.salary_derived_pay)}</strong>{data.salary_prorated ? ' (prorated for this range)' : ''}.</>
                  : (data.salary_note ? ` ${data.salary_note}` : '')}
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
              <div className="card" style={{ padding: '8px 14px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>Scheduled</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{fmtN(data.total_scheduled_hours)}h</div>
              </div>
              <div className="card" style={{ padding: '8px 14px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>Actual (reconciles to the report row)</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{fmtN(data.total_actual_hours)}h</div>
              </div>
              {data.pay_rate ? (
                <div className="card" style={{ padding: '8px 14px' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>Pay rate</div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{fmt(data.pay_rate)}/hr</div>
                </div>
              ) : null}
              {data.total_manual_hours_not_in_payroll ? (
                <div className="card" style={{ padding: '8px 14px' }} title="storeops.manual_hours entries — /payroll never reads this table, so these are NOT part of the report row's Actual Hrs">
                  <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>Manual hours (not in payroll)</div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{fmtN(data.total_manual_hours_not_in_payroll)}h</div>
                </div>
              ) : null}
              {data.total_lunch_deduction_hours ? (
                <div className="card" style={{ padding: '8px 14px' }} title="Auto lunch-break deduction — already netted OUT of Actual above (HONESTY: shown here as its own explicit line, never a silent subtraction). See ⚙ Lunch Break Settings on the Time Clock page.">
                  <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>Lunch deducted (auto)</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#b45309' }}>− {fmtN(data.total_lunch_deduction_hours)}h</div>
                </div>
              ) : null}
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'var(--surface2)' }}>
                  {['Date', 'Store', 'Shift (scheduled → effective)', 'Punches (in → out)', 'Manual', 'Day total'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.days.map((d: any) => (
                  <tr key={d.work_date} style={{ background: d.double_counted ? 'rgba(234,88,12,0.08)' : undefined, borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 8px', fontWeight: 600 }}>{d.work_date}</td>
                    <td style={{ padding: '6px 8px' }}>{d.store_code || '—'}</td>
                    <td style={{ padding: '6px 8px' }}>
                      {d.shift ? (
                        <span>
                          {fmtN(d.shift.scheduled_hours)}h → {fmtN(d.shift.effective_hours)}h
                          {d.shift.counted === false ? <span style={{ color: 'var(--text3)' }}> (not counted — phantom)</span> : null}
                          {d.shift.edited ? <span title="Manually edited — see Payroll Change Log" style={{ marginLeft: 4 }}>✎</span> : null}
                        </span>
                      ) : '—'}
                    </td>
                    <td style={{ padding: '6px 8px' }}>
                      {d.punches.length === 0 ? '—' : d.punches.map((p: any) => (
                        <div key={p.id} style={{ opacity: p.counted ? 1 : 0.5 }}>
                          {fmtTime(p.clock_in)} → {p.clock_out ? fmtTime(p.clock_out) : 'open'}
                          {p.hours != null ? ` (${fmtN(p.hours)}h)` : ''}
                          {!p.counted ? <span style={{ color: 'var(--text3)' }}> not counted</span> : null}
                          {p.edited ? <span title="Manually edited — see Payroll Change Log" style={{ marginLeft: 4 }}>✎</span> : null}
                        </div>
                      ))}
                    </td>
                    <td style={{ padding: '6px 8px' }}>
                      {d.manual.length === 0 ? '—' : d.manual.map((m: any) => (
                        <div key={m.id} style={{ opacity: 0.7 }} title="Not in the payroll total below — /payroll never reads manual hours adjustments">
                          {m.hours > 0 ? '+' : ''}{fmtN(m.hours)}h — {m.reason} ✎
                          <span style={{ color: 'var(--text3)', fontSize: 11 }}> (not in payroll total)</span>
                        </div>
                      ))}
                    </td>
                    <td style={{ padding: '6px 8px', fontWeight: 700 }}>
                      {fmtN(d.actual_hours)}h
                      {d.lunch_deduction_applied ? (
                        <div style={{ fontSize: 11, fontWeight: 400, color: '#b45309' }}>− {fmtN(d.lunch_deduction_hours)}h lunch (auto)</div>
                      ) : d.lunch_deduction_skip_reason === 'real_break_present' ? (
                        <div style={{ fontSize: 11, fontWeight: 400, color: 'var(--text3)' }} title="Gapped punch-pairs — a real break already happened, or this is a split shift; auto-deduction never applies on top">no auto lunch (real break/split shift)</div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {data.days.some((d: any) => d.double_counted) && (
              <div className="card" style={{ marginTop: 12, padding: '10px 14px', fontSize: 12, background: 'var(--surface2)' }}>
                ⚠ Highlighted day(s) counted a scheduled shift AND a separate clock punch on the same
                day — a known data artifact (schedule-created shift vs. kiosk punch identity mismatch),
                not necessarily a real double shift. See {' '}
                <a href="/storeops/payroll-change-log" style={{ color: 'var(--accent)' }}>the Payroll Change Log</a> for any manual corrections on this employee.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
