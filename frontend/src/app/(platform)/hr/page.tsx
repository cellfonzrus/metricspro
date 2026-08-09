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
import { PAY_BASES, PAY_BASIS_LABEL, periodPayPreviewLabel, type PayBasis } from '../storeops/lib/pay-basis'

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
  // Salary pay-basis (owner directive 2026-07-27, migrations 416/417). `ppType` is the tenant's
  // configured pay_period_type ('weekly' | 'biweekly') for the live preview label only — the
  // AUTHORITATIVE per-period figure always comes from the backend (GET /payroll, GET /compensation).
  const [ppType, setPpType] = useState<string | null>(null)

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
  useEffect(() => {
    api('/api/v1/core/tenant-settings').then((r: any) => setPpType(r?.settings?.pay_period_type || null)).catch(() => {})
  }, [])

  // pay_basis/pay_amount/termination_date only exist once migrations 416/417 have run — GET
  // /storeops/employees is a `select("*")`, so the KEYS simply won't be present on any row until
  // then. Gating the whole salary UI (and never sending those fields in a PATCH) on this is what
  // keeps a pre-migration tenant's existing "Pay $/hr" save flow byte-identical (no unknown-column
  // 500 from a PATCH body that includes a not-yet-existing field).
  const salaryFieldsAvailable = emps.some(e => Object.prototype.hasOwnProperty.call(e, 'pay_basis'))

  // ---- lunch-break auto-deduction, per-employee override (owner directive 2026-07-27, Deliverable 3)
  // ---- SAME permission posture as pay_rate on this same tab (org-scoped only, no extra gate); a
  // DEDICATED endpoint (PUT /employees/{id}/lunch-config), never folded into the generic pay PATCH, so
  // a tenant that hasn't run migration 418 yet can never have an unrelated pay-rate save fail because
  // of it. 'Default' = inherit the tenant-wide setting (⚙ Lunch Break Settings on the Time Clock page).
  const [lunchEdit, setLunchEdit] = useState<Record<number, { mode: 'default' | 'on' | 'off'; minutes: string; busy?: boolean; msg?: string }>>({})
  function lunchStateFor(e: any) {
    return lunchEdit[e.id] || {
      mode: e.lunch_deduction_enabled === true ? 'on' : e.lunch_deduction_enabled === false ? 'off' : 'default',
      minutes: e.lunch_deduction_minutes != null ? String(e.lunch_deduction_minutes) : '',
    }
  }
  async function saveLunch(e: any) {
    const st = lunchStateFor(e)
    setLunchEdit(s => ({ ...s, [e.id]: { ...st, busy: true, msg: '' } }))
    try {
      // 'off'/'default' never send a stale minutes value from a previous 'on' edit — enabled=false/null
      // already wins (harmless either way), but only 'on' has any business sending a minutes override.
      const body = { enabled: st.mode === 'default' ? null : st.mode === 'on', minutes: st.mode === 'on' && st.minutes.trim() !== '' ? Number(st.minutes) : null }
      await api(`/api/v1/storeops/employees/${e.id}/lunch-config`, { method: 'PUT', body: JSON.stringify(body) })
      setLunchEdit(s => ({ ...s, [e.id]: { ...st, busy: false, msg: '✅' } }))
    } catch (err: any) {
      setLunchEdit(s => ({ ...s, [e.id]: { ...st, busy: false, msg: '❌ ' + (err?.message || err) } }))
    }
  }

  // ---- face recognition, per-employee assignment + consent (owner directive 2026-08-09, mig 420) ----
  // "it should be assigned per employee". Same shape and permission posture as the lunch override
  // right next to it, and the same isolation reason for a DEDICATED endpoint
  // (PUT /employees/{id}/face-config): a tenant without migration 420 must never have an unrelated
  // pay-rate save fail. 'Default' = follow the tenant master switch's default; 'Off' excludes this
  // person even while the feature is on; a 'declined' consent excludes them regardless of assignment.
  const [faceEdit, setFaceEdit] = useState<Record<number, { mode: 'default' | 'on' | 'off'; consent: '' | 'signed' | 'declined'; busy?: boolean; msg?: string }>>({})
  const faceModeOf = (e: any) => e.face_recognition_enabled === true ? 'on' : e.face_recognition_enabled === false ? 'off' : 'default'
  const faceConsentOf = (e: any) => (e.face_consent_status === 'signed' || e.face_consent_status === 'declined') ? e.face_consent_status : ''
  function faceStateFor(e: any) {
    return faceEdit[e.id] || { mode: faceModeOf(e) as 'default' | 'on' | 'off', consent: faceConsentOf(e) as '' | 'signed' | 'declined' }
  }
  async function saveFace(e: any) {
    const st = faceStateFor(e)
    setFaceEdit(s => ({ ...s, [e.id]: { ...st, busy: true, msg: '' } }))
    try {
      const body = { enabled: st.mode === 'default' ? null : st.mode === 'on', consent: st.consent === '' ? null : st.consent }
      const r = await api(`/api/v1/storeops/employees/${e.id}/face-config`, { method: 'PUT', body: JSON.stringify(body) })
      // Re-seat the row from the SAVED values so the dirty check clears (the consent timestamp/source
      // are server-generated — echoing the request back would leave the row looking permanently dirty).
      setEmps(es => es.map(x => x.id === e.id ? { ...x, face_recognition_enabled: r.face_recognition_enabled, face_consent_status: r.face_consent_status, face_consent_at: r.face_consent_at, face_consent_source: r.face_consent_source } : x))
      setFaceEdit(s => { const n = { ...s }; delete n[e.id]; return n })
    } catch (err: any) {
      setFaceEdit(s => ({ ...s, [e.id]: { ...st, busy: false, msg: '❌ ' + (err?.message || err) } }))
    }
  }

  // ---- pay editing (HR owns pay rates; StoreOps no longer shows them) ----
  const setEmpField = (id: number, patch: any) => setEmps(es => es.map(e => e.id === id ? { ...e, ...patch, _dirty: true } : e))
  const setPay = (id: number, v: string) => setEmpField(id, { pay_rate: v })
  async function savePay(e: any) {
    setRowBusy(e.id); setMsg(''); setErr('')
    const body: any = { pay_rate: Number(e.pay_rate) || 0 }
    if (salaryFieldsAvailable && Object.prototype.hasOwnProperty.call(e, 'pay_basis')) {
      body.pay_basis = e.pay_basis || 'hourly'
      body.pay_amount = e.pay_basis && e.pay_basis !== 'hourly' ? (e.pay_amount === '' || e.pay_amount == null ? null : Number(e.pay_amount)) : null
      body.termination_date = e.termination_date || null
    }
    try {
      await api(`/api/v1/storeops/employees/${e.id}`, { method: 'PATCH', body: JSON.stringify(body) })
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
          { header: 'Pay basis', get: (r: any) => (r.pay_basis && r.pay_basis !== 'hourly') ? (PAY_BASIS_LABEL[r.pay_basis as PayBasis] || r.pay_basis) : 'Hourly' },
          { header: 'Pay $/hr', get: (r: any) => (r.pay_basis && r.pay_basis !== 'hourly') ? '' : r.pay_rate, align: 'right' as const },
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
                        <td style={tdR}>{fmt(r.base_salary)}
                          {r.pay_basis && r.pay_basis !== 'hourly' && <span title={`${PAY_BASIS_LABEL[r.pay_basis as PayBasis] || r.pay_basis}${r.salary_prorated ? ' · prorated for this period' : ''}`} style={{ fontSize: 10, color: 'var(--text3)', marginLeft: 4 }}>({r.pay_basis}{r.salary_prorated ? ' ◔' : ''})</span>}
                          {r.chargebacks > 0 ? <span title="chargebacks deducted" style={{ fontSize: 10, color: '#b42318' }}> −{fmt(r.chargebacks)}</span> : null}</td>
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
                <span style={{ fontSize: 13, color: 'var(--text3)' }}>
                  Pay is set here in HR and flows to payroll, total comp and the employee dashboard.
                  {salaryFieldsAvailable && ' Choose Hourly, or a flat Weekly/Monthly/Annual salary — the company pay period below shows what that converts to per pay period.'}
                </span>
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
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Name', 'Emp ID', 'Home store', 'Role', 'Pay basis',
                      ...(salaryFieldsAvailable ? ['Salary amount', 'Pay $/hr', 'Terminated'] : ['Pay $/hr']),
                      'Lunch (auto-deduct)', 'Face recognition', 'Email', 'Phone', ''].map(h => <th key={h} style={th}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {emps.map((e: any) => {
                      const basis: PayBasis = (salaryFieldsAvailable ? (e.pay_basis || 'hourly') : 'hourly') as PayBasis
                      const isSalaried = salaryFieldsAvailable && basis !== 'hourly'
                      const hasBasisField = salaryFieldsAvailable && Object.prototype.hasOwnProperty.call(e, 'pay_basis')
                      const ls = lunchStateFor(e)
                      const lunchDirty = ls.mode !== (e.lunch_deduction_enabled === true ? 'on' : e.lunch_deduction_enabled === false ? 'off' : 'default')
                        || ls.minutes !== (e.lunch_deduction_minutes != null ? String(e.lunch_deduction_minutes) : '')
                      const fs = faceStateFor(e)
                      const faceDirty = fs.mode !== faceModeOf(e) || fs.consent !== faceConsentOf(e)
                      return (
                      <tr key={e.id}>
                        <td style={{ ...td, fontWeight: 600 }}>{e.name}</td>
                        <td style={td}>{e.employee_id || '—'}</td>
                        <td style={td}>{e.home_store || '—'}</td>
                        <td style={td}>{e.role || '—'}</td>
                        <td style={td}>
                          {hasBasisField ? (
                            <select value={basis} style={{ padding: '4px 6px', borderRadius: 6, border: `1px solid ${e._dirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 13, background: 'var(--surface)' }}
                              onChange={ev => setEmpField(e.id, { pay_basis: ev.target.value })}>
                              {PAY_BASES.map(b => <option key={b} value={b}>{PAY_BASIS_LABEL[b]}</option>)}
                            </select>
                          ) : <span style={{ color: 'var(--text3)' }}>Hourly</span>}
                        </td>
                        {salaryFieldsAvailable && (
                          <td style={td}>
                            {isSalaried ? (
                              <div>
                                <input type="number" step="0.01" placeholder="amount" value={e.pay_amount ?? ''}
                                  onChange={ev => setEmpField(e.id, { pay_amount: ev.target.value })}
                                  style={{ width: 90, padding: '4px 6px', borderRadius: 6, border: `1px solid ${e._dirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 13, background: 'var(--surface)' }} />
                                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                                  {periodPayPreviewLabel(basis, e.pay_amount === '' || e.pay_amount == null ? null : Number(e.pay_amount), ppType) || '—'}
                                </div>
                              </div>
                            ) : <span style={{ color: 'var(--text3)' }}>—</span>}
                          </td>
                        )}
                        <td style={td}>
                          <input type="number" step="0.01" value={e.pay_rate ?? ''} disabled={isSalaried}
                            title={isSalaried ? 'Pay is derived from the salary amount, not this rate' : undefined}
                            onChange={ev => setPay(e.id, ev.target.value)}
                            style={{ width: 90, padding: '4px 6px', borderRadius: 6, border: `1px solid ${e._dirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 13, background: isSalaried ? 'var(--surface2)' : 'var(--surface)', opacity: isSalaried ? 0.6 : 1 }} />
                        </td>
                        {salaryFieldsAvailable && (
                          <td style={td}>
                            <input type="date" value={e.termination_date || ''}
                              onChange={ev => setEmpField(e.id, { termination_date: ev.target.value })}
                              style={{ padding: '4px 6px', borderRadius: 6, border: `1px solid ${e._dirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 12, background: 'var(--surface)' }} />
                          </td>
                        )}
                        <td style={td}>
                          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                            <select value={ls.mode} onChange={ev => setLunchEdit(s => ({ ...s, [e.id]: { ...ls, mode: ev.target.value as any, msg: '' } }))}
                              style={{ padding: '4px 6px', borderRadius: 6, border: `1px solid ${lunchDirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 12, background: 'var(--surface)' }}>
                              <option value="default">Default (tenant)</option>
                              <option value="on">On</option>
                              <option value="off">Off</option>
                            </select>
                            {ls.mode === 'on' && (
                              <input type="number" min={0} placeholder="min" value={ls.minutes}
                                onChange={ev => setLunchEdit(s => ({ ...s, [e.id]: { ...ls, minutes: ev.target.value, msg: '' } }))}
                                style={{ width: 56, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }} />
                            )}
                            {lunchDirty && <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 8px' }} disabled={ls.busy} onClick={() => saveLunch(e)}>{ls.busy ? '…' : '💾'}</button>}
                            {ls.msg && <span style={{ fontSize: 11 }}>{ls.msg}</span>}
                          </div>
                        </td>
                        <td style={td}>
                          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                            <select value={fs.mode} onChange={ev => setFaceEdit(s => ({ ...s, [e.id]: { ...fs, mode: ev.target.value as any, msg: '' } }))}
                              title="Whether the kiosk verifies this person by face. Only has any effect while the tenant master switch (Time Clock → ⚙ Face Recognition) is on."
                              style={{ padding: '4px 6px', borderRadius: 6, border: `1px solid ${faceDirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 12, background: 'var(--surface)' }}>
                              <option value="default">Default (tenant)</option>
                              <option value="on">On</option>
                              <option value="off">Off</option>
                            </select>
                            <select value={fs.consent} onChange={ev => setFaceEdit(s => ({ ...s, [e.id]: { ...fs, consent: ev.target.value as any, msg: '' } }))}
                              title={e.face_consent_at ? `Consent ${e.face_consent_status} on ${String(e.face_consent_at).slice(0, 10)} (${e.face_consent_source || 'source not recorded'})` : 'No biometric consent recorded for this person'}
                              style={{ padding: '4px 6px', borderRadius: 6, border: `1px solid ${faceDirty ? 'var(--accent)' : 'var(--border)'}`, fontSize: 12, background: 'var(--surface)' }}>
                              <option value="">Consent: none</option>
                              <option value="signed">Consent: signed</option>
                              <option value="declined">Consent: declined</option>
                            </select>
                            {faceDirty && <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 8px' }} disabled={fs.busy} onClick={() => saveFace(e)}>{fs.busy ? '…' : '💾'}</button>}
                            {fs.msg && <span style={{ fontSize: 11 }}>{fs.msg}</span>}
                          </div>
                        </td>
                        <td style={td}>{e.email || '—'}</td>
                        <td style={td}>{e.phone || '—'}</td>
                        <td style={td}>{e._dirty && <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} disabled={rowBusy === e.id} onClick={() => savePay(e)}>{rowBusy === e.id ? '…' : '💾'}</button>}</td>
                      </tr>
                      )
                    })}
                    {/* Merge note (Gate-1 hand-fix): the salary-basis columns (added 2026-07-27) and
                        the lunch-deduction column (parallel branch, same day) both bumped the header
                        count independently — colSpan must match the UNION header row above, not
                        either side's original count alone. */}
                    {/* +1 again 2026-08-09 for the face-recognition column (migration 420). */}
                    {emps.length === 0 && <tr><td style={td} colSpan={salaryFieldsAvailable ? 13 : 11}><span style={{ color: 'var(--text3)' }}>No employees in your area.</span></td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {tab === 'payroll' && (
            <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  <th style={th}>Employee</th><th style={th}>Store</th><th style={th}>Pay basis</th>
                  <th style={{ ...th, textAlign: 'right' }}>Pay $/hr</th><th style={{ ...th, textAlign: 'right' }}>Sched hrs</th>
                  <th style={{ ...th, textAlign: 'right' }}>Actual hrs</th><th style={{ ...th, textAlign: 'right' }}>Lunch (auto)</th>
                  <th style={{ ...th, textAlign: 'right' }}>Sched pay</th>
                  <th style={{ ...th, textAlign: 'right' }}>Actual pay</th>
                </tr></thead>
                <tbody>
                  {payroll.map((r: any) => {
                    const salaried = r.pay_basis && r.pay_basis !== 'hourly'
                    return (
                    <tr key={r.employee_id || r.name}>
                      <td style={{ ...td, fontWeight: 600 }}>{r.name}</td>
                      <td style={td}>{r.store || '—'}</td>
                      <td style={td}>
                        {salaried ? (PAY_BASIS_LABEL[r.pay_basis as PayBasis] || r.pay_basis) : 'Hourly'}
                        {r.salary_prorated && <span title="Prorated for this range (mid-period hire/term or a range not aligned to a full pay period)" style={{ marginLeft: 4, fontSize: 11, color: 'var(--text3)' }}>◔</span>}
                        {r.salary_note && <span title={r.salary_note} style={{ marginLeft: 4 }}>⚠</span>}
                      </td>
                      <td style={tdR}>{salaried ? '—' : fmt(r.pay_rate)}</td>
                      <td style={tdR}>{r.scheduled_hours}</td>
                      <td style={tdR}>{r.actual_hours}</td>
                      {/* Actual hrs above is already NET of this — HONESTY (Deliverable 3): shown as its own line, never a silent subtraction. */}
                      <td style={tdR}>{r.lunch_deduction_hours ? `− ${Number(r.lunch_deduction_hours).toFixed(2)}` : '—'}</td>
                      <td style={tdR}>{fmt(r.scheduled_pay)}</td>
                      <td style={{ ...tdR, fontWeight: 700 }}>{fmt(r.actual_pay)}</td>
                    </tr>
                    )
                  })}
                  {/* Merge note (Gate-1 hand-fix, 2nd instance of the same class of bug the reviewer
                      flagged in the Employees & Pay tab above): "Pay basis" (this branch) + "Lunch
                      (auto)" (parallel branch) each independently bumped 7->8 columns, so git's line
                      merge found colSpan={8} on BOTH sides and silently kept it — the union header row
                      (Employee/Store/Pay basis/Pay $/hr/Sched hrs/Actual hrs/Lunch (auto)/Sched pay/
                      Actual pay) is actually 9. */}
                  {payroll.length === 0 && <tr><td style={td} colSpan={9}><span style={{ color: 'var(--text3)' }}>No payroll rows for {period} (need shifts entered).</span></td></tr>}
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
