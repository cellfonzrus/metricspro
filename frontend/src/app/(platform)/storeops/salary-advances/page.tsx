'use client'
// Salary Advances (owner directive 2026-08-04, EEP package — docs/specs/envelope-expense-payout.md).
// OWNER RULE (verbatim intent): cash salary paid from the daily-closing envelope NEVER changes what
// payroll counts — the Payroll Report (clock-in based) stays the ONE wages truth. This page shows,
// per employee, what they've EARNED (the same clock-in basis as Payroll) vs what's been PAID IN CASH
// from the envelope, and records new advances. Only the running EXCESS of cash paid over earned posts
// to the P&L as a separate "Additional Payroll" line — the preview card at the bottom shows exactly
// that figure, computed live, never a duplicate of payroll_gross.
//
// RULE FIVE (§3d): standard filter bar (period range / store / rep) drives the summary table, the
// export, and (via its period) the Additional Payroll preview. RULE FOUR (§3c): both tables export
// Excel/PDF/email/WhatsApp. RULE THREE (§3b): employee/store pickers are EntityPicker, never free text.
import { Fragment, useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP, CONFIG } from '@/lib/cache'
import EntityPicker from '@/components/EntityPicker'
import ReportShell from '@/components/ReportShell'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { currentPeriodFromSettingsResponse, monthRange, rangeLabel, type PayPeriodSettings } from '../lib/pay-period'

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }
const chip: React.CSSProperties = { padding: '5px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)', cursor: 'pointer' }
const $ = (n: number) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

type OwedEmployee = {
  employee_id: string; name: string; store: string; pay_basis: string
  days: { date: string; hours: number; basis: 'actual' | 'scheduled'; rate: number; owed: number }[]
  owed_total: number; cash_paid_total: number; balance: number
}

export default function SalaryAdvancesPage() {
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rangeReady, setRangeReady] = useState(false)
  const [ppSettings, setPpSettings] = useState<PayPeriodSettings | null>(null)
  const [owed, setOwed] = useState<OwedEmployee[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<string>('')   // employee_id whose days[] drill-down is open

  const [stores, setStores] = useState<any[]>([])
  const [employees, setEmployees] = useState<any[]>([])

  const [history, setHistory] = useState<any[]>([])
  const [historyAvailable, setHistoryAvailable] = useState(true)

  const [addlPeriod, setAddlPeriod] = useState('')
  const [addl, setAddl] = useState<any>(null)
  const [addlLoading, setAddlLoading] = useState(false)

  // record-advance form
  const [showForm, setShowForm] = useState(false)
  const [fEmp, setFEmp] = useState<string | null>(null)
  const [fAmount, setFAmount] = useState('')
  const [fDate, setFDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [fStore, setFStore] = useState<string | null>(null)
  const [fRef, setFRef] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => {
      if (cancelled) return
      const cur = currentPeriodFromSettingsResponse(r)
      if (cur) { setPpSettings(cur.settings); setFilt(f => ({ ...f, period: cur.period.start, periodTo: cur.period.end })) }
      else { const mr = monthRange(0); setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end })) }
    }).catch(() => {
      const mr = monthRange(0)
      if (!cancelled) setFilt(f => ({ ...f, period: mr.start, periodTo: mr.end }))
    }).finally(() => { if (!cancelled) setRangeReady(true) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    // include_inactive=true: this report is a HISTORICAL surface (RULE FIVE filter bar) — a store
    // closed today may still own past rows in this range, and the market lookup below must still
    // resolve it. GET /stores now defaults to active-only (2026-08-06 disabled-T-store fix).
    apiCached('/api/v1/storeops/stores?include_inactive=true', LOOKUP).then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees', LOOKUP).then((r: any) => setEmployees(Array.isArray(r) ? r : [])).catch(() => {})
  }, [])

  const load = useCallback(() => {
    if (!rangeReady || !filt.period || !filt.periodTo) return
    setLoading(true)
    api(`/api/v1/storeops/salary-owed?start=${filt.period}&end=${filt.periodTo}`)
      .then((r: any) => setOwed(Array.isArray(r?.employees) ? r.employees : []))
      .catch((e: any) => { setOwed([]); setMsg('Load failed (run migration 419?): ' + (e?.message || e)) })
      .finally(() => setLoading(false))
    api(`/api/v1/storeops/salary-advance/history?start=${filt.period}&end=${filt.periodTo}`)
      .then((r: any) => { setHistory(r?.items || []); setHistoryAvailable(r?.available !== false) })
      .catch(() => { setHistory([]); setHistoryAvailable(false) })
  }, [rangeReady, filt.period, filt.periodTo])
  useEffect(() => { load() }, [load])

  // Additional Payroll preview period follows the filter's END date's calendar month (matches the
  // period the P&L push attributes to — see backend router's paid_date.strftime('%Y-%m')).
  useEffect(() => {
    if (filt.periodTo) setAddlPeriod(String(filt.periodTo).slice(0, 7))
  }, [filt.periodTo])

  const loadAddl = useCallback(() => {
    if (!addlPeriod) return
    setAddlLoading(true)
    api(`/api/v1/storeops/salary-advance/additional-payroll/${addlPeriod}`)
      .then((r: any) => setAddl(r))
      .catch(() => setAddl(null))
      .finally(() => setAddlLoading(false))
  }, [addlPeriod])
  useEffect(() => { loadAddl() }, [loadAddl])

  const storeMarket = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code) m[s.store_code] = s.market || ''
    return m
  }, [stores])
  const storeOptions = useMemo(() => stores
    .filter(s => s.store_code)
    .map(s => ({ id: s.store_code, label: s.store_code + (s.is_active === false ? ' (inactive)' : ''), sublabel: s.address || s.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [stores])
  const marketOptions = useMemo(() =>
    Array.from(new Set(stores.map(s => s.market).filter(Boolean) as string[])).sort(), [stores])
  const employeeOptions = useMemo(() => employees
    .filter((e: any) => e.employee_id)
    .map((e: any) => ({ id: e.employee_id, label: e.name || e.employee_id, sublabel: e.email || undefined }))
    .sort((a: any, b: any) => a.label.localeCompare(b.label)), [employees])
  const empEmail = useMemo(() => {
    const m: Record<string, string> = {}
    for (const e of employees) if (e.employee_id) m[e.employee_id] = e.email || ''
    return m
  }, [employees])
  const repOptions = useMemo(() => optionsFromRows(owed, {
    rep: r => r.name, repEmail: r => empEmail[r.employee_id],
  }).reps, [owed, empEmail])

  const visibleOwed = useMemo(() => filterRows(owed, filt, {
    store: r => r.store, market: r => storeMarket[r.store] || '', rep: r => r.name,
  }), [owed, filt, storeMarket])

  const visibleHistory = useMemo(() => filterRows(history, filt, {
    store: r => r.store_code, market: r => storeMarket[r.store_code] || '', rep: r => r.employee_name || r.employee_id,
    date: r => r.paid_date,
  }), [history, filt, storeMarket])

  const totals = visibleOwed.reduce((a, r) => ({
    owed: a.owed + (r.owed_total || 0), paid: a.paid + (r.cash_paid_total || 0),
  }), { owed: 0, paid: 0 })

  const periodName = rangeLabel(filt.period || '', filt.periodTo || '')

  const owedCols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: (r: OwedEmployee) => r.name },
    { header: 'Store', field: 'store', role: 'store', get: (r: OwedEmployee) => r.store || '' },
    { header: 'Pay basis', field: 'pay_basis', get: (r: OwedEmployee) => r.pay_basis },
    { header: 'Earned (clock-in)', field: 'owed_total', money: true, get: (r: OwedEmployee) => r.owed_total },
    { header: 'Cash Paid', field: 'cash_paid_total', money: true, get: (r: OwedEmployee) => r.cash_paid_total },
    { header: 'Balance', field: 'balance', money: true, get: (r: OwedEmployee) => r.balance },
  ]
  const historyCols: ExportColumn[] = [
    { header: 'Paid date', field: 'paid_date', role: 'date', type: 'date', get: (r: any) => r.paid_date },
    { header: 'Employee', field: 'employee', role: 'rep', get: (r: any) => r.employee_name || r.employee_id },
    { header: 'Store', field: 'store_code', role: 'store', get: (r: any) => r.store_code || '' },
    { header: 'Amount', field: 'amount', money: true, get: (r: any) => r.amount },
    { header: 'Method', field: 'method', get: (r: any) => r.method },
    { header: 'Withdrawal ref', field: 'withdrawal_ref', get: (r: any) => r.withdrawal_ref || '' },
    { header: 'Recorded by', field: 'recorded_by', get: (r: any) => r.recorded_by || '' },
  ]

  function setRange(start: string, end: string) { setFilt(f => ({ ...f, period: start, periodTo: end })) }
  function onFilterChange(v: StandardFilterValue) {
    setFilt(v.period || v.periodTo ? v : { ...v, period: filt.period, periodTo: filt.periodTo })
  }

  async function submitAdvance() {
    setMsg('')
    const amt = parseFloat(fAmount)
    if (!fEmp) { setMsg('❌ Pick an employee.'); return }
    if (!amt || amt <= 0) { setMsg('❌ Enter an amount greater than 0.'); return }
    if (!fDate) { setMsg('❌ Pick a paid date.'); return }
    setSaving(true)
    try {
      await api('/api/v1/storeops/salary-advance/record', {
        method: 'POST',
        body: JSON.stringify({ employee_id: fEmp, amount: amt, paid_date: fDate, store_code: fStore, withdrawal_ref: fRef || undefined }),
      })
      setMsg('✅ Advance recorded.')
      setFAmount(''); setFRef(''); setShowForm(false)
      load(); loadAddl()
    } catch (e: any) {
      setMsg('❌ ' + (e?.message || e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💵 Salary Advances</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Per-employee earned salary (clock-in based — the SAME basis as the Payroll Report) vs cash
          paid out of the daily-closing envelope. Cash payments are <b>advances</b>, never a change to
          Payroll itself; only cumulative excess of cash paid over earned posts to the P&L, as
          "Additional Payroll" below. {periodName}.
        </p>
      </div>

      <StandardFilterBar
        value={filt} onChange={onFilterChange}
        periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        right={
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <button style={chip} onClick={() => { const r = monthRange(0); setRange(r.start, r.end) }}>This month</button>
            <button style={chip} onClick={() => { const r = monthRange(-1); setRange(r.start, r.end) }}>Last month</button>
            <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={() => setShowForm(s => !s)}>
              {showForm ? 'Cancel' : '➕ Record Advance'}
            </button>
          </div>
        }
      />

      {showForm && (
        <div className="card" style={{ marginBottom: 12, padding: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ fontSize: 12 }}>Employee<br />
            <EntityPicker options={employeeOptions} value={fEmp} onChange={setFEmp} placeholder="Employee…" width={220} />
          </label>
          <label style={{ fontSize: 12 }}>Amount<br />
            <input style={sel} type="number" min="0.01" step="0.01" value={fAmount} onChange={e => setFAmount(e.target.value)} placeholder="0.00" />
          </label>
          <label style={{ fontSize: 12 }}>Paid date<br />
            <input style={sel} type="date" value={fDate} onChange={e => setFDate(e.target.value)} />
          </label>
          <label style={{ fontSize: 12 }}>Store (envelope)<br />
            <EntityPicker options={storeOptions} value={fStore} onChange={setFStore} placeholder="Store…" width={180} />
          </label>
          <label style={{ fontSize: 12 }}>Withdrawal ref (optional)<br />
            <input style={sel} value={fRef} onChange={e => setFRef(e.target.value)} placeholder="envelope ref…" />
          </label>
          <button className="btn btn-primary" disabled={saving} onClick={submitAdvance}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      )}

      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 10, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="badge" style={{ fontSize: 12 }}>Earned {$(totals.owed)}</span>
        <span className="badge" style={{ fontSize: 12 }}>Cash Paid {$(totals.paid)}</span>
        <span className="badge" style={{ fontSize: 12 }}>Balance {$(totals.owed - totals.paid)}</span>
        <div style={{ flex: 1 }} />
        <ReportExportBar title="Salary Owed vs Cash Advances" subtitle={periodName}
          filename={`salary-owed-${filt.period}_${filt.periodTo}`} columns={owedCols} rows={visibleOwed} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : visibleOwed.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
          No clocked activity or salaried employees for {periodName}. (If this is unexpected, confirm
          migration 419 has run — see the Salary Advances handoff.)
        </div>
      ) : (
        <div className="card" style={{ overflowX: 'auto', marginBottom: 18 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', fontSize: 12, color: 'var(--text3)' }}>
                <th style={cell}></th>
                <th style={cell}>Employee</th>
                <th style={cell}>Store</th>
                <th style={cell}>Pay basis</th>
                <th style={cell}>Earned (clock-in)</th>
                <th style={cell}>Cash Paid</th>
                <th style={cell}>Balance</th>
              </tr>
            </thead>
            <tbody>
              {visibleOwed.map(r => (
                <Fragment key={r.employee_id}>
                  <tr style={{ cursor: 'pointer' }}
                    onClick={() => setExpanded(x => x === r.employee_id ? '' : r.employee_id)}>
                    <td style={cell}>{expanded === r.employee_id ? '▾' : '▸'}</td>
                    <td style={cell}>{r.name}</td>
                    <td style={cell}>{r.store}</td>
                    <td style={cell}>{r.pay_basis}</td>
                    <td style={cell}>{$(r.owed_total)}</td>
                    <td style={cell}>{$(r.cash_paid_total)}</td>
                    <td style={{ ...cell, color: r.balance < 0 ? 'var(--danger, #c0392b)' : undefined }}>{$(r.balance)}</td>
                  </tr>
                  {expanded === r.employee_id && (
                    <tr>
                      <td></td>
                      <td colSpan={6} style={{ ...cell, background: 'var(--surface2)' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ textAlign: 'left', fontSize: 11, color: 'var(--text3)' }}>
                              <th style={cell}>Date</th><th style={cell}>Hours</th><th style={cell}>Basis</th>
                              <th style={cell}>Rate</th><th style={cell}>Owed</th>
                            </tr>
                          </thead>
                          <tbody>
                            {r.days.map(d => (
                              <tr key={d.date}>
                                <td style={cell}>{d.date}</td>
                                <td style={cell}>{d.hours.toFixed(2)}</td>
                                <td style={cell}>{d.basis === 'scheduled' ? 'Scheduled (est.)' : 'Clocked'}</td>
                                <td style={cell}>{$(d.rate)}</td>
                                <td style={cell}>{$(d.owed)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ padding: 14, marginBottom: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>📊 Additional Payroll preview — {addlPeriod || '—'}</h3>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={loadAddl} disabled={addlLoading}>
            {addlLoading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px' }}>
          The excess of cumulative cash paid over cumulative clock-in-derived earnings, through the end
          of {addlPeriod || 'this period'} — the ONLY figure from this page that ever hits the P&L
          (as its own "Additional Payroll" line, never folded into wages). $0 unless an employee's cash
          advances have outrun what they've actually earned.
        </p>
        {!addl?.available ? (
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>Not available yet (migration 419 pending).</div>
        ) : addl.cells.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>$0 — no employee's cash paid exceeds what they've earned.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ textAlign: 'left', fontSize: 11, color: 'var(--text3)' }}>
                <th style={cell}>Employee</th><th style={cell}>Store</th>
                <th style={cell}>Earned to date</th><th style={cell}>Cash paid to date</th><th style={cell}>Excess</th>
              </tr>
            </thead>
            <tbody>
              {addl.employees.filter((e: any) => e.excess > 0).map((e: any) => (
                <tr key={e.employee_id}>
                  <td style={cell}>{e.name}</td><td style={cell}>{e.store}</td>
                  <td style={cell}>{$(e.earned_to_date)}</td><td style={cell}>{$(e.cash_paid_to_date)}</td>
                  <td style={cell}>{$(e.excess)}</td>
                </tr>
              ))}
              <tr><td colSpan={4} style={{ ...cell, fontWeight: 600 }}>Total (posts to P&L)</td><td style={{ ...cell, fontWeight: 600 }}>{$(addl.total)}</td></tr>
            </tbody>
          </table>
        )}
      </div>

      <h2 style={{ fontSize: 17, fontWeight: 700, margin: '4px 0 8px' }}>Advance History</h2>
      {!historyAvailable && (
        <div className="card" style={{ marginBottom: 12, padding: '10px 14px', fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)' }}>
          ℹ️ The Salary Advance ledger isn't set up yet on this tenant (migration 419) — no advances can
          be recorded or shown until it runs.
        </div>
      )}
      {historyAvailable && (
        <ReportShell
          title="Salary Advance History" subtitle={periodName}
          filename={`salary-advance-history-${filt.period}_${filt.periodTo}`}
          columns={historyCols} rows={visibleHistory} defaultGroupBy="Employee"
        />
      )}
    </div>
  )
}
