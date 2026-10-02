'use client'
// HR module — a consolidated, permission-gated VIEW of salary + commission + people data. Everything
// is span-scoped server-side (a manager sees only their area) and the underlying data still lives in
// StoreOps / CommCalc — this is the single place to see total compensation. Pay is SET here, per row, on
// the Employees & Pay tab (index §19.35); StoreOps Admin edits no pay. Gated by the `hr` module permission
// (default OFF for managers).
//
// The tab lives in the URL (`?tab=employees`, index §19.40) through the one reader `lib/useUrlTab.ts`, so
// the "Employees & Pay" menu entry, ScreenLinks and notices open the tab directly — including when /hr is
// already open.
import { useState, useEffect, useCallback, useRef, Suspense } from 'react'
import { api, ORG_ID, fmt } from '@/lib/client'
import { apiCached, LOOKUP, CONFIG, invalidateApiCache } from '@/lib/cache'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import StatTile from '@/components/StatTile'
import { PAY_BASES, PAY_BASIS_LABEL, periodPayPreviewLabel, type PayBasis } from '../storeops/lib/pay-basis'
import { planRowSave, runRowSave, commitSaved, rowSaveMessage, rowDirty, dirtySlices, pendingRowCount, rebaseRows, fieldChanged } from '@/lib/rowSave'
import { HR_EMPLOYEE_ROW_SLICES } from '@/lib/employeeRowSlices'
import { useUnsavedGuard } from '@/lib/useUnsavedGuard'
import { useUrlTab } from '@/lib/useUrlTab'
import ScreenLink from '@/components/ScreenLink'

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

// The tabs this page declares — the ONLY keys a `/hr?tab=` link may name (harness_nav_deep_link_lock.py).
const HR_TABS = ['comp', 'employees', 'payroll', 'timeoff'] as const
type Tab = typeof HR_TABS[number]

// `useUrlTab` reads useSearchParams, so the body sits inside a Suspense boundary (lib/useUrlTab.ts).
export default function HRPage() {
  return (
    <Suspense fallback={<div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div>}>
      <HRPageBody />
    </Suspense>
  )
}

function HRPageBody() {
  const { period } = usePeriod()
  const [tab, setTab] = useUrlTab(HR_TABS, 'comp')
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

  // Last-saved snapshot per employee row (index §19.35) — see "ONE ROW, ONE SAVE" below.
  const [snaps, setSnaps] = useState<Record<string, any>>({})
  const snapsRef = useRef<Record<string, any>>({})
  // A fresh roster read (tab open, period change, bulk upload) never drops typed edits: dirty rows keep
  // them on top of the fresh values and stay marked unsaved.
  const seatRoster = useCallback((fresh: any[]) => {
    const prevSnaps = snapsRef.current
    setEmps(prev => rebaseRows(prev, (r: any) => prevSnaps[String(r.id)], fresh, (r: any) => String(r.id), HR_EMPLOYEE_ROW_SLICES))
    const next = Object.fromEntries(fresh.map((f: any) => [String(f.id), f]))
    snapsRef.current = next
    setSnaps(next)
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      if (tab === 'comp') setComp(await api(`/api/v1/hr/compensation?org_id=${ORG_ID}&period=${encodeURIComponent(period)}`))
      else if (tab === 'employees') seatRoster(await apiCached('/api/v1/storeops/employees', LOOKUP) || [])
      else if (tab === 'payroll') setPayroll(await api(`/api/v1/storeops/payroll?month=${periodToMonth(period)}`) || [])
      else if (tab === 'timeoff') setTimeoff(await api('/api/v1/storeops/time-off') || [])
    } catch (e: any) { setErr(e?.message || 'Failed to load') }
    setLoading(false)
  }, [tab, period, seatRoster])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => setPpType(r?.settings?.pay_period_type || null)).catch(() => {})
  }, [])

  // pay_basis/pay_amount/termination_date only exist once migrations 416/417 have run — GET
  // /storeops/employees is a `select("*")`, so the KEYS simply won't be present on any row until
  // then. Gating the whole salary UI (and never sending those fields in a PATCH) on this is what
  // keeps a pre-migration tenant's existing "Pay $/hr" save flow byte-identical (no unknown-column
  // 500 from a PATCH body that includes a not-yet-existing field).
  const salaryFieldsAvailable = emps.some(e => Object.prototype.hasOwnProperty.call(e, 'pay_basis'))

  // ---- ONE ROW, ONE SAVE (owner report 2026-09-29, index §19.35) --------------------------------
  // Owner: "i just saved hourly salary in vzone but it did not save when i came back". This tab used
  // to give every row THREE independent 💾 buttons — pay (in the LAST column, past Email and Phone,
  // off the right edge of the table), Lunch and Face — each saving only its own slice. The owner typed
  // hourly rates, set Lunch to On and pressed the 💾 beside Lunch; the access log shows four
  // `PUT …/lunch-config` and NO pay PATCH: the rates never left the browser. Now every edit lives on
  // the row itself, `lib/employeeRowSlices.ts` says which request persists which field, and the row's
  // ONE Save (first column, beside the name) plans a request for EVERY edited slice via
  // `lib/rowSave.ts::planRowSave`. The result message names what was saved and what was not; a row
  // with unsaved edits is marked, counted, and guarded against leaving (`useUnsavedGuard`).
  // Lunch and face keep their DEDICATED endpoints (a tenant without migration 418/420 must never have
  // a pay save fail because of them) — runRowSave runs each slice independently for the same reason.
  // Permission posture unchanged: pay is manager- and pay-visibility-gated server-side.
  // `snaps` (state) is what the render compares against; `snapsRef` mirrors it for the async paths
  // (the roster loader is a memoised callback, a save resolves after later renders).
  const snapOf = (e: any) => snaps[String(e.id)]
  const pendingRows = tab === 'employees' ? pendingRowCount(emps, snapOf, HR_EMPLOYEE_ROW_SLICES) : 0
  const { confirmDiscard } = useUnsavedGuard(pendingRows)
  const setEmpField = (id: number, patch: any) => setEmps(es => es.map(e => e.id === id ? { ...e, ...patch } : e))
  const setPay = (id: number, v: string) => setEmpField(id, { pay_rate: v })
  const [rowMsg, setRowMsg] = useState<Record<string, { ok: boolean; text: string }>>({})

  async function saveRow(e: any) {
    const snap = snapsRef.current[String(e.id)]
    const writes = planRowSave(e, snap, HR_EMPLOYEE_ROW_SLICES, { salaryFieldsAvailable })
    if (!writes.length) return
    setRowBusy(e.id); setMsg(''); setErr('')
    const result = await runRowSave(writes, w => api(w.path, { method: w.method, body: JSON.stringify(w.body) }))
    if (result.saved.length) invalidateApiCache('/api/v1/storeops/employees')   // cached roster read must self-heal after an edit
    const committed = commitSaved(e, snap || e, HR_EMPLOYEE_ROW_SLICES, result)
    snapsRef.current = { ...snapsRef.current, [String(e.id)]: committed.snap }
    setSnaps(snapsRef.current)
    setEmps(es => es.map(x => x.id === e.id ? { ...x, ...committed.back } : x))
    const text = rowSaveMessage(e.name, result)
    setRowMsg(m => ({ ...m, [String(e.id)]: { ok: !result.failed.length, text } }))
    if (result.failed.length) setErr(text); else setMsg(text)
    setRowBusy('')
  }
  async function saveAllRows() {
    for (const e of emps) if (rowDirty(e, snapOf(e), HR_EMPLOYEE_ROW_SLICES)) await saveRow(e)
  }
  function switchTab(t: Tab) {
    if (t !== tab && tab === 'employees' && !confirmDiscard()) return
    setTab(t)
  }
  // Lunch / face controls edit the ROW (no separate buttons). Mode 'default' = inherit the tenant
  // setting (null); minutes accompany only an explicit 'on'.
  const lunchModeOf = (e: any) => e.lunch_deduction_enabled === true ? 'on' : e.lunch_deduction_enabled === false ? 'off' : 'default'
  const faceModeOf = (e: any) => e.face_recognition_enabled === true ? 'on' : e.face_recognition_enabled === false ? 'off' : 'default'
  const faceConsentOf = (e: any) => (e.face_consent_status === 'signed' || e.face_consent_status === 'declined') ? e.face_consent_status : ''
  const modeValue = (m: string) => m === 'on' ? true : m === 'off' ? false : null

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
      invalidateApiCache('/api/v1/storeops/employees')   // cached roster read must self-heal before the reload
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
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Salary, payroll and total compensation in one place — scoped to your area. Set each person&apos;s pay on
          the <ScreenLink to="employees_pay">Employees &amp; Pay</ScreenLink> tab.
          Configure employer payroll tax + burden items on the <a href="/hr/payroll-expenses" style={{ color: 'var(--accent,#2563eb)' }}>Payroll Expenses</a> page.
          Manage disciplinary/shortage/performance letters in <a href="/hr/letters" style={{ color: 'var(--accent,#2563eb)' }}>HR Letters</a> —
          send one from <a href="/hr/letters/send" style={{ color: 'var(--accent,#2563eb)' }}>Send a Letter</a>, review the{' '}
          <a href="/hr/letters/queue" style={{ color: 'var(--accent,#2563eb)' }}>Approval Queue</a>, or see the{' '}
          <a href="/hr/letters/sent" style={{ color: 'var(--accent,#2563eb)' }}>Sent Log</a>.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        {TABS.map(t => (
          <button key={t.k} onClick={() => switchTab(t.k)} style={{ padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: tab === t.k ? 'var(--accent)' : 'var(--surface)', color: tab === t.k ? '#fff' : 'var(--text2)' }}>{t.label}</button>
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
                <div className="stat-grid" style={{ marginBottom: 16 }}>
                  <StatTile hero label="Total comp" value={fmt(comp.totals.total_comp)} accent="var(--accent)"
                    sub="base + commission − chargebacks" />
                  <StatTile label="Base salary" value={fmt(comp.totals.base_salary)} accent="#2563eb" />
                  <StatTile label="Commission" value={fmt(comp.totals.commission)} accent="var(--green)" />
                  <StatTile label="Annualized (proj.)" value={fmt(comp.totals.annualized)} accent="#7c3aed" />
                  <StatTile label="People" value={comp.totals.employees?.toLocaleString?.() ?? comp.totals.employees} accent="#d97706" />
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
              {pendingRows > 0 && (
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, padding: '8px 12px', borderRadius: 8, border: '1px solid #f59e0b', background: 'rgba(245,158,11,0.08)' }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#b45309' }}>● {pendingRows} row{pendingRows === 1 ? '' : 's'} with unsaved changes</span>
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>Nothing is saved until you press Save — leaving this page asks first.</span>
                  <div style={{ flex: 1 }} />
                  <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }} disabled={rowBusy !== ''} onClick={saveAllRows}>💾 Save all</button>
                </div>
              )}
              <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Name', 'Emp ID', 'Home store', 'Role', 'Pay basis',
                      ...(salaryFieldsAvailable ? ['Salary amount', 'Pay $/hr', 'Terminated'] : ['Pay $/hr']),
                      'Lunch (auto-deduct)', 'Face recognition', 'Email', 'Phone'].map(h => <th key={h} style={th}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {emps.map((e: any) => {
                      const basis: PayBasis = (salaryFieldsAvailable ? (e.pay_basis || 'hourly') : 'hourly') as PayBasis
                      const isSalaried = salaryFieldsAvailable && basis !== 'hourly'
                      const hasBasisField = salaryFieldsAvailable && Object.prototype.hasOwnProperty.call(e, 'pay_basis')
                      const snap = snapOf(e)
                      const edited = (f: string) => fieldChanged(e, snap, f)
                      const bd = (f: string) => `1px solid ${edited(f) ? 'var(--accent)' : 'var(--border)'}`
                      const pending = dirtySlices(e, snap, HR_EMPLOYEE_ROW_SLICES)
                      const lunchMode = lunchModeOf(e)
                      const rm = rowMsg[String(e.id)]
                      return (
                      <tr key={e.id}>
                        {/* THE row's one Save lives HERE, beside the name — never off the right edge. */}
                        <td style={{ ...td, fontWeight: 600, whiteSpace: 'nowrap' }}>
                          {e.name}
                          {pending.length > 0 && (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4, fontWeight: 400 }}>
                              <button className="btn btn-primary" style={{ fontSize: 12, padding: '3px 10px' }} disabled={rowBusy === e.id}
                                title={`Saves every edited field on this row: ${pending.map(p => p.label).join(', ')}`}
                                onClick={() => saveRow(e)}>{rowBusy === e.id ? '…' : '💾 Save'}</button>
                              <span style={{ fontSize: 11, color: '#b45309' }}>● unsaved: {pending.map(p => p.label).join(', ')}</span>
                            </div>
                          )}
                          {rm && pending.length === 0 && <div style={{ fontSize: 11, fontWeight: 400, marginTop: 2, color: rm.ok ? '#15803d' : '#b42318' }}>{rm.ok ? '✅ ' : '❌ '}{rm.text}</div>}
                          {rm && !rm.ok && pending.length > 0 && <div style={{ fontSize: 11, fontWeight: 400, marginTop: 2, color: '#b42318' }}>❌ {rm.text}</div>}
                        </td>
                        <td style={td}>{e.employee_id || '—'}</td>
                        <td style={td}>{e.home_store || '—'}</td>
                        <td style={td}>{e.role || '—'}</td>
                        <td style={td}>
                          {hasBasisField ? (
                            <select value={basis} style={{ padding: '4px 6px', borderRadius: 6, border: bd('pay_basis'), fontSize: 13, background: 'var(--surface)' }}
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
                                  style={{ width: 90, padding: '4px 6px', borderRadius: 6, border: bd('pay_amount'), fontSize: 13, background: 'var(--surface)' }} />
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
                            style={{ width: 90, padding: '4px 6px', borderRadius: 6, border: bd('pay_rate'), fontSize: 13, background: isSalaried ? 'var(--surface2)' : 'var(--surface)', opacity: isSalaried ? 0.6 : 1 }} />
                        </td>
                        {salaryFieldsAvailable && (
                          <td style={td}>
                            <input type="date" value={e.termination_date || ''}
                              onChange={ev => setEmpField(e.id, { termination_date: ev.target.value })}
                              style={{ padding: '4px 6px', borderRadius: 6, border: bd('termination_date'), fontSize: 12, background: 'var(--surface)' }} />
                          </td>
                        )}
                        <td style={td}>
                          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                            <select value={lunchMode} onChange={ev => setEmpField(e.id, { lunch_deduction_enabled: modeValue(ev.target.value), ...(ev.target.value === 'on' ? {} : { lunch_deduction_minutes: null }) })}
                              style={{ padding: '4px 6px', borderRadius: 6, border: bd('lunch_deduction_enabled'), fontSize: 12, background: 'var(--surface)' }}>
                              <option value="default">Default (tenant)</option>
                              <option value="on">On</option>
                              <option value="off">Off</option>
                            </select>
                            {lunchMode === 'on' && (
                              <input type="number" min={0} placeholder="min" value={e.lunch_deduction_minutes ?? ''}
                                onChange={ev => setEmpField(e.id, { lunch_deduction_minutes: ev.target.value })}
                                style={{ width: 56, padding: '4px 6px', borderRadius: 6, border: bd('lunch_deduction_minutes'), fontSize: 12, background: 'var(--surface)' }} />
                            )}
                          </div>
                        </td>
                        <td style={td}>
                          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                            <select value={faceModeOf(e)} onChange={ev => setEmpField(e.id, { face_recognition_enabled: modeValue(ev.target.value) })}
                              title="Whether the kiosk verifies this person by face. Only has any effect while the tenant master switch (Time Clock → ⚙ Face Recognition) is on."
                              style={{ padding: '4px 6px', borderRadius: 6, border: bd('face_recognition_enabled'), fontSize: 12, background: 'var(--surface)' }}>
                              <option value="default">Default (tenant)</option>
                              <option value="on">On</option>
                              <option value="off">Off</option>
                            </select>
                            <select value={faceConsentOf(e)} onChange={ev => setEmpField(e.id, { face_consent_status: ev.target.value || null })}
                              title={e.face_consent_at ? `Consent ${e.face_consent_status} on ${String(e.face_consent_at).slice(0, 10)} (${e.face_consent_source || 'source not recorded'})` : 'No biometric consent recorded for this person'}
                              style={{ padding: '4px 6px', borderRadius: 6, border: bd('face_consent_status'), fontSize: 12, background: 'var(--surface)' }}>
                              <option value="">Consent: none</option>
                              <option value="signed">Consent: signed</option>
                              <option value="declined">Consent: declined</option>
                            </select>
                          </div>
                        </td>
                        <td style={td}>{e.email || '—'}</td>
                        <td style={td}>{e.phone || '—'}</td>
                      </tr>
                      )
                    })}
                    {/* Merge note (Gate-1 hand-fix): the salary-basis columns (added 2026-07-27) and
                        the lunch-deduction column (parallel branch, same day) both bumped the header
                        count independently — colSpan must match the UNION header row above, not
                        either side's original count alone. */}
                    {/* +1 again 2026-08-09 for the face-recognition column (migration 420); −1 on
                        2026-09-29 (§19.35): the trailing save column is gone — the row's one Save
                        sits in the Name cell. */}
                    {emps.length === 0 && <tr><td style={td} colSpan={salaryFieldsAvailable ? 12 : 10}><span style={{ color: 'var(--text3)' }}>No employees in your area.</span></td></tr>}
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
