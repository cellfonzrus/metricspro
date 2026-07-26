'use client'
// HR module — a consolidated, permission-gated VIEW of salary + commission + people data. Everything
// is span-scoped server-side (a manager sees only their area) and the underlying data still lives in
// StoreOps / CommCalc — this is the single place to see total compensation. Editing pay stays on
// StoreOps Admin. Gated by the `hr` module permission (default OFF for managers).
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

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
  const [msg, setMsg] = useState('')
  const [rowBusy, setRowBusy] = useState<number | ''>('')
  const [upBusy, setUpBusy] = useState(false)

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

  // ---- pay editing (HR owns pay rates; StoreOps no longer shows them) ----
  const setPay = (id: number, v: string) => setEmps(es => es.map(e => e.id === id ? { ...e, pay_rate: v, _dirty: true } : e))
  async function savePay(e: any) {
    setRowBusy(e.id); setMsg(''); setErr('')
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify({ pay_rate: Number(e.pay_rate) || 0 }) })
      setEmps(es => es.map(x => x.id === e.id ? { ...x, _dirty: false } : x))
      setMsg(`Saved pay for ${e.name}`)
    } catch (err: any) { setErr('Save failed: ' + (err?.message || err)) } finally { setRowBusy('') }
  }
  async function downloadPayTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['employee_id', 'name', 'pay_rate'], ...emps.map((e: any) => [e.employee_id || '', e.name, e.pay_rate ?? ''])]
    const ws = XLSX.utils.aoa_to_sheet(aoa); ws['!cols'] = [{ wch: 14 }, { wch: 24 }, { wch: 10 }]
    const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, 'Payscale'); XLSX.writeFile(wb, 'payscale-template.xlsx')
  }
  async function uploadPayscale(file: File) {
    setUpBusy(true); setMsg('Reading sheet…'); setErr('')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => { for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim(); return '' }
      const rows = raw.map(r => ({ employee_id: pick(r, ['employee_id', 'emp id', 'id']), name: pick(r, ['name', 'employee']), pay_rate: pick(r, ['pay_rate', 'pay rate', 'rate', 'pay']) }))
        .filter(r => r.pay_rate !== '' && (r.employee_id || r.name))
      if (!rows.length) { setMsg('No valid rows (need pay_rate + employee_id/name).'); setUpBusy(false); return }
      const res = await api('/api/v1/storeops/employees/bulk-payscale', { method: 'POST', body: JSON.stringify({ rows }) })
      setMsg(`Pay rates updated: ${res.updated}${(res.errors || []).length ? ` · ${res.errors.length} skipped` : ''}.`)
      await load()
    } catch (err: any) { setErr('Upload failed: ' + (err?.message || err)) } finally { setUpBusy(false) }
  }

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
          Configure employer payroll tax + burden items on the <a href="/hr/payroll-expenses" style={{ color: 'var(--accent,#2563eb)' }}>Payroll Expenses</a> page.
          Manage disciplinary/shortage/performance letters in <a href="/hr/letters" style={{ color: 'var(--accent,#2563eb)' }}>HR Letters</a> —
          send one from <a href="/hr/letters/send" style={{ color: 'var(--accent,#2563eb)' }}>Send a Letter</a>, review the{' '}
          <a href="/hr/letters/queue" style={{ color: 'var(--accent,#2563eb)' }}>Approval Queue</a>, or see the{' '}
          <a href="/hr/letters/sent" style={{ color: 'var(--accent,#2563eb)' }}>Sent Log</a>.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        {TABS.map(t => (
          <button key={t.k} onClick={() => setTab(t.k)} style={{ padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: tab === t.k ? 'var(--accent)' : 'var(--surface)', color: tab === t.k ? '#fff' : 'var(--text2)' }}>{t.label}</button>
        ))}
        {msg && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{msg}</span>}
        <span style={{ flex: 1 }} />
        {tab === 'comp' && comp?.rows?.length > 0 && <><ExportButtons payload={compPayload} compact /><SendReportButton exportPayload={compPayload} compact /></>}
      </div>

      {err && <div className="card" style={{ padding: 12, color: '#c0392b', borderColor: '#c0392b', marginBottom: 12 }}>{err}</div>}
      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <>
          {tab === 'comp' && (
            <>
              {comp?.totals && (
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                  {[['Base salary', comp.totals.base_salary], ['Commission', comp.totals.commission], ['Total comp', comp.totals.total_comp], ['Annualized (proj.)', comp.totals.annualized]].map(([l, v]: any) => (
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
              <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 8px' }}>
                Base salary = the period&apos;s hours × pay rate. Total comp = base + commission − chargebacks. Annualized = total comp × 12.
              </p>
              <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    <th style={th}>Employee</th><th style={th}>Store</th>
                    <th style={{ ...th, textAlign: 'right' }}>Base salary</th><th style={{ ...th, textAlign: 'right' }}>Commission</th>
                    <th style={{ ...th, textAlign: 'right' }}>Total comp</th><th style={{ ...th, textAlign: 'right' }}>Annualized (proj.)</th>
                  </tr></thead>
                  <tbody>
                    {(comp?.rows || []).map((r: any) => (
                      <tr key={r.employee_id || r.name}>
                        <td style={{ ...td, fontWeight: 600 }}>{r.name}</td>
                        <td style={td}>{r.store || '—'}</td>
                        <td style={tdR}>{fmt(r.base_salary)}{r.chargebacks > 0 ? <span title="chargebacks deducted" style={{ fontSize: 10, color: '#b42318' }}> −{fmt(r.chargebacks)}</span> : null}</td>
                        <td style={tdR}>{fmt(r.commission)}</td>
                        <td style={{ ...tdR, fontWeight: 700 }}>{fmt(r.total_comp)}</td>
                        <td style={tdR}>{fmt(r.annualized)}</td>
                      </tr>
                    ))}
                    {(!comp?.rows || comp.rows.length === 0) && <tr><td style={td} colSpan={6}><span style={{ color: 'var(--text3)' }}>No compensation data for {period}.</span></td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {tab === 'employees' && (
            <>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--text3)' }}>Pay rates are set here in HR and flow to payroll, total comp and the employee dashboard.</span>
                <div style={{ flex: 1 }} />
                <span style={{ fontSize: 13, fontWeight: 600 }}>Bulk pay rates:</span>
                <button className="btn" onClick={downloadPayTemplate}>⬇️ Template</button>
                <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
                  {upBusy ? '⏳ Uploading…' : '⬆️ Upload pay rates'}
                  <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
                    onChange={e => { const f = e.target.files?.[0]; if (f) uploadPayscale(f); e.currentTarget.value = '' }} />
                </label>
              </div>
              <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Name', 'Emp ID', 'Home store', 'Role', 'Pay $/hr', 'Email', 'Phone', ''].map(h => <th key={h} style={th}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {emps.map((e: any) => (
                      <tr key={e.id}>
                        <td style={{ ...td, fontWeight: 600 }}>{e.name}</td>
                        <td style={td}>{e.employee_id || '—'}</td>
                        <td style={td}>{e.home_store || '—'}</td>
                        <td style={td}>{e.role || '—'}</td>
                        <td style={td}>
                          <input type="number" step="0.01" value={e.pay_rate ?? ''} onChange={ev => setPay(e.id, ev.target.value)}
                            style={{ width: 90, padding: '4px 6px', borderRadius: 6, border: `1px solid ${e._dirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 13, background: 'var(--surface)' }} />
                        </td>
                        <td style={td}>{e.email || '—'}</td>
                        <td style={td}>{e.phone || '—'}</td>
                        <td style={td}>{e._dirty && <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} disabled={rowBusy === e.id} onClick={() => savePay(e)}>{rowBusy === e.id ? '…' : '💾'}</button>}</td>
                      </tr>
                    ))}
                    {emps.length === 0 && <tr><td style={td} colSpan={8}><span style={{ color: 'var(--text3)' }}>No employees in your area.</span></td></tr>}
                  </tbody>
                </table>
              </div>
            </>
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
