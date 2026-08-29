'use client'
// Weekly payroll-hours approval — DM approves, then HR, then it goes to whoever pays (migration 431).
//
// OWNER DIRECTIVE 2026-08-10: "dm needs to approve the hours for the employees who have worked and
// then the hr approves it to send to accounting or the related parties to pay it."
//
// RULE FIVE: the standard filter bar drives the table AND the export (what you see is what exports).
// RULE FOUR: ReportExportBar gives Excel / PDF / Print / email / WhatsApp on the visible rows.
// RULE THREE: the payer is PICKED from the configured list, never typed.
//
// The server is the authority on every gate here (HR cannot approve before the DM; only an admin can
// override or dispatch). This page shows the right controls for the person looking at it, but it is
// the API that refuses — the UI hiding a button is never the security boundary.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import { ReportExportBar, type ExportColumn } from '@/components/ReportExportBar'
import type { StandardFilterValue } from '@/lib/standard-filters'

type Row = {
  employee_id: string; name?: string; store?: string
  scheduled_hours?: number | null; hours_source?: number | null
  hours_approved?: number | null; hours_effective?: number | null; hours_corrected?: boolean
  pay_rate?: number; pay_effective?: number
  // An active employee the clock holds nothing for, plus the days the POS proves they worked.
  no_clock_record?: boolean; worked_days_evidence?: string[]
  dm_status: string; dm_by?: string | null; dm_at?: string | null; dm_note?: string | null
  hr_status: string; hr_by?: string | null; hr_at?: string | null; hr_note?: string | null
  payer_id?: string | null; payer_name?: string | null; payer_kind?: string | null; payer_from?: string | null
  override_by?: string | null; override_reason?: string | null
  // mig 746. hours_worked is GROSS; hours_source is already NET of lunch (the payroll screen
  // subtracts it there), so payable = hours_source + adjustment, NOT hours_worked − lunch + adj
  // computed off hours_source — that would take lunch out twice.
  hours_worked?: number | null; lunch_hours?: number | null
  adjustment_hours?: number | null; adjustment_reason?: string | null
  hours_payable?: number | null; hours_drifted?: boolean
  worked_at_approval?: number | null; lunch_at_approval?: number | null
  dispatch_status?: string; dispatched_at?: string | null; dispatch_to?: string | null
  held?: boolean; payable?: boolean
}
type Payer = { id: string; name: string; kind: string; email?: string | null; is_default?: boolean }
// What a reviewer has typed but not yet submitted: an hours correction and/or an adjustment, each
// with its own reason. Both reasons are required by the server when the number actually moves.
type Edit = { hours: string; reason: string; adj: string; adjReason: string }
const EMPTY_EDIT: Edit = { hours: '', reason: '', adj: '', adjReason: '' }

const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 16 }
const btn: React.CSSProperties = { padding: '7px 13px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', fontSize: 13, cursor: 'pointer', fontWeight: 600 }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent)', color: '#fff', border: 'none' }
const money = (n?: number | null) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function Pill({ status }: { status: string }) {
  const map: Record<string, [string, string, string]> = {
    approved: ['#dcfce7', '#15803d', 'Approved'],
    pending: ['#f1f5f9', '#64748b', 'Pending'],
    sent_back: ['#fef3c7', '#b45309', 'Sent back'],
  }
  const [bg, fg, label] = map[status] || map.pending
  return <span style={{ background: bg, color: fg, borderRadius: 999, padding: '2px 9px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>{label}</span>
}

export default function PayrollApprovalsPage() {
  const { permissions } = useAuth()
  const isAdmin = !!(permissions?.modules?.admin || permissions?.scope === 'all')

  const [f, setF] = useState<StandardFilterValue>({ period: '', periodTo: '', stores: [], markets: [], reps: [] })
  const [rows, setRows] = useState<Row[]>([])
  const [payers, setPayers] = useState<Payer[]>([])
  const [totals, setTotals] = useState<any>(null)
  const [period, setPeriod] = useState<{ start: string; end: string } | null>(null)
  // The pay CYCLE the shown period belongs to (server-resolved from the tenant's own settings, the same
  // ones the schedule grid uses). `payday` is present only when the range IS a configured period.
  const [cycle, setCycle] = useState<{ pay_period_type?: string; week_starts_on?: string; payday?: string | null; matches_cycle?: boolean } | null>(null)
  // Owner 2026-08-11: a DM / market manager approves HOURS and must not see anyone's pay scale. The
  // server withholds the values; this only controls whether the columns are rendered at all.
  const [canSeePay, setCanSeePay] = useState(true)
  const [ready, setReady] = useState(true)
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [chip, setChip] = useState('all')
  const [sel, setSel] = useState<Record<string, boolean>>({})
  const [edits, setEdits] = useState<Record<string, Edit>>({})

  // One updater for all four fields, so adding the adjustment pair cannot drop the hours pair on
  // the floor the way two independent setEdits calls would.
  function setEdit(id: string, patch: Partial<Edit>) {
    setEdits(s => ({ ...s, [id]: { ...(s[id] ?? EMPTY_EDIT), ...patch } }))
  }

  const load = useCallback(async () => {
    setErr(''); setMsg('')
    const qs = new URLSearchParams()
    if (f.period && f.periodTo) { qs.set('start', f.period); qs.set('end', f.periodTo) }
    if (f.stores.length) qs.set('store_code', f.stores.join(','))
    if (f.markets.length) qs.set('market', f.markets.join(','))
    if (f.reps.length) qs.set('employee_id', f.reps.join(','))
    try {
      const d = await api(`/api/v1/storeops/payroll/approvals?${qs.toString()}`)
      setReady(d?.ready !== false); setNote(d?.note || '')
      setRows(d?.rows || []); setPayers(d?.payers || []); setTotals(d?.totals || null)
      if (d?.period_start) setPeriod({ start: d.period_start, end: d.period_end })
      setCycle(d?.cycle || null)
      // Server decides; the UI only mirrors it. Absent key => treat as allowed (an older backend
      // still sends the rates, so hiding the columns would blank data the caller is entitled to).
      setCanSeePay(d?.can_see_pay_rates !== false)
      setSel({}); setEdits({})
    } catch (e: any) { setErr(e?.message || 'Could not load the pay period') }
  }, [f.period, f.periodTo, f.stores, f.markets, f.reps])

  useEffect(() => { load() }, [load])

  // The API already applied every filter, so the chips are the only client-side narrowing.
  const shown = useMemo(() => rows.filter(r => (
    chip === 'all' ? true
      : chip === 'pending_dm' ? r.dm_status === 'pending'
      : chip === 'pending_hr' ? r.dm_status === 'approved' && r.hr_status === 'pending'
      : chip === 'approved' ? r.dm_status === 'approved' && r.hr_status === 'approved'
      : chip === 'held' ? r.held
      : true)), [rows, chip])

  const selIds = Object.keys(sel).filter(k => sel[k])
  const allShown = shown.length > 0 && shown.every(r => sel[r.employee_id])

  async function post(path: string, body: any, okMsg: (d: any) => string) {
    if (!period) return
    setBusy(true); setErr(''); setMsg('')
    try {
      const d = await api(`/api/v1/storeops/payroll/${path}`, {
        method: 'POST',
        body: JSON.stringify({ period_start: period.start, period_end: period.end, ...body }),
      })
      const errs = (d?.errors || []).filter(Boolean)
      if (errs.length) setErr(errs.map((x: any) => `${x.employee_id || ''}: ${x.error}`).join(' · '))
      setMsg(okMsg(d))
      await load()
    } catch (e: any) { setErr(e?.message || 'That did not go through') }
    setBusy(false)
  }

  function decide(stage: 'dm' | 'hr', action: 'approve' | 'send_back' | 'reset') {
    if (!selIds.length) return
    let note = ''
    if (action === 'send_back') {
      note = window.prompt('Why is this going back? The DM/store will see this note.') || ''
      if (!note.trim()) return
    }
    // A DM approval carries any inline hour correction the reviewer typed, with its reason.
    const body = {
      stage,
      rows: selIds.map(id => {
        const e = edits[id]
        const changed = stage === 'dm' && e && e.hours !== ''
        const adjusted = stage === 'dm' && e && e.adj !== ''
        return { employee_id: id, action, note: note || undefined,
                 ...(changed ? { hours_approved: e.hours, reason: e.reason } : {}),
                 ...(adjusted ? { adjustment_hours: e.adj, adjustment_reason: e.adjReason } : {}) }
      }),
    }
    const verb = action === 'approve' ? 'Approved' : action === 'send_back' ? 'Sent back' : 'Reset'
    post('approvals/decide', body, d => `${verb} ${d.applied} of ${selIds.length}.`)
  }

  function setPayer(payerId: string) {
    if (!selIds.length) return
    post('approvals/payer', { employee_ids: selIds, payer_id: payerId || null },
      d => `Routed ${d.updated} employee(s).`)
  }

  function override() {
    if (!selIds.length) return
    const reason = window.prompt('These hours are not fully approved. Why are you paying them anyway?\n(recorded against your account)') || ''
    if (!reason.trim()) return
    post('approvals/override', { employee_ids: selIds, reason }, d => `Override recorded for ${d.updated}.`)
  }

  async function dispatch(dry: boolean) {
    if (!period) return
    setBusy(true); setErr(''); setMsg('')
    try {
      const d = await api('/api/v1/storeops/payroll/approvals/dispatch', {
        method: 'POST',
        body: JSON.stringify({ period_start: period.start, period_end: period.end, dry_run: dry }),
      })
      if (dry) {
        const lines = (d.would_send || []).map((x: any) => `${x.label} (${x.to}): ${x.employees} employee(s), ${money(x.pay)}`)
        setMsg(lines.length ? `Would send — ${lines.join(' · ')}` : 'Nothing to send.')
      } else {
        const ok = (d.sent || []).reduce((a: number, x: any) => a + x.employees, 0)
        setMsg(`Sent ${ok} employee(s) to ${(d.sent || []).length} payer(s).`)
      }
      const bad = [...(d.unroutable || []).map((x: any) => `${x.name}: ${x.why}`),
                   ...(d.failed || []).map((x: any) => `${x.to}: ${x.error}`)]
      if (bad.length) setErr(bad.join(' · '))
      if (!dry) await load()
    } catch (e: any) { setErr(e?.message || 'Send failed') }
    setBusy(false)
  }

  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: (r: Row) => r.name || r.employee_id },
    { header: 'Employee ID', field: 'employee_id', get: (r: Row) => r.employee_id },
    { header: 'Store', field: 'store', role: 'store', get: (r: Row) => r.store || '' },
    { header: 'Scheduled', field: 'scheduled_hours', type: 'number', get: (r: Row) => r.scheduled_hours ?? '' },
    { header: 'Worked Hours', field: 'hours_worked', type: 'number', get: (r: Row) => r.hours_worked ?? '' },
    { header: 'Lunch', field: 'lunch_hours', type: 'number', get: (r: Row) => r.lunch_hours ?? '' },
    { header: 'Adjustment', field: 'adjustment_hours', type: 'number', get: (r: Row) => r.adjustment_hours ?? '' },
    { header: 'Adjustment reason', field: 'adjustment_reason', get: (r: Row) => r.adjustment_reason || '' },
    { header: 'Payable Hours', field: 'hours_payable', type: 'number', get: (r: Row) => r.hours_payable ?? '' },
    { header: 'No clock record', field: 'no_clock_record', get: (r: Row) => (r.no_clock_record ? 'yes' : '') },
    { header: 'Days worked (POS)', field: 'worked_days_evidence',
      get: (r: Row) => (r.worked_days_evidence || []).join(' ') },
    { header: 'Computed Hours (net)', field: 'hours_source', type: 'number', get: (r: Row) => r.hours_source ?? '' },
    { header: 'Approved Hours', field: 'hours_effective', type: 'number', get: (r: Row) => r.hours_effective ?? '' },
    { header: 'Corrected', field: 'hours_corrected', get: (r: Row) => (r.hours_corrected ? 'yes' : '') },
    // Pay-scale columns are DROPPED for a caller who may not see rates (owner 2026-08-11), not blanked
    // — the server already withholds the values, so an exported blank column would only advertise them.
    ...(canSeePay ? [
      { header: 'Rate', field: 'pay_rate', type: 'number', get: (r: Row) => r.pay_rate ?? '' },
      { header: 'Pay', field: 'pay_effective', type: 'number', get: (r: Row) => r.pay_effective ?? '' },
    ] as ExportColumn[] : []),
    { header: 'DM', field: 'dm_status', get: (r: Row) => r.dm_status },
    { header: 'HR', field: 'hr_status', get: (r: Row) => r.hr_status },
    { header: 'Paid by', field: 'payer_name', get: (r: Row) => r.payer_name || 'not routed' },
    { header: 'Held', field: 'held', get: (r: Row) => (r.held ? 'HELD — not approved' : '') },
    { header: 'Override reason', field: 'override_reason', get: (r: Row) => r.override_reason || '' },
    { header: 'Sent to', field: 'dispatch_to', get: (r: Row) => r.dispatch_to || '' },
  ]

  const chips: [string, string, number][] = [
    ['all', 'All', rows.length],
    ['pending_dm', 'Waiting on DM', rows.filter(r => r.dm_status === 'pending').length],
    ['pending_hr', 'Waiting on HR', rows.filter(r => r.dm_status === 'approved' && r.hr_status === 'pending').length],
    ['approved', 'Fully approved', rows.filter(r => r.dm_status === 'approved' && r.hr_status === 'approved').length],
    ['held', 'Held from payout', rows.filter(r => r.held).length],
  ]

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Hours Approval</h1>
        {period && (
          <span style={{ color: 'var(--text2)', fontSize: 14 }}>
            {/* Never the hardcoded word "week": a biweekly tenant's period is a fortnight, and calling
                it a week is how the board stopped matching the schedule and the payday. */}
            {cycle?.matches_cycle === false ? 'custom range' : 'pay period'}{' '}
            <b>{period.start}</b> → <b>{period.end}</b>
            {cycle?.payday && <> · payable <b>{cycle.payday}</b></>}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <Link href="/storeops/payroll/payers" style={{ ...btn, textDecoration: 'none', color: 'var(--text1)' }}>⚙️ Who pays</Link>
      </div>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 14px', maxWidth: '78ch' }}>
        The district manager checks the closed pay period&apos;s hours, corrects anything wrong, and approves. HR approves
        after them, then sends each payer the people they pay. Hours can only be changed here with a reason,
        and every change is written to the payroll change log.
      </p>

      {!ready && <div style={{ ...card, background: '#fff7ed', borderColor: '#fdba74', color: '#9a3412', marginBottom: 12 }}>{note}</div>}
      {err && <div style={{ ...card, background: '#fef2f2', borderColor: '#fca5a5', color: '#991b1b', marginBottom: 12, fontSize: 13 }}>{err}</div>}
      {msg && <div style={{ ...card, background: '#ecfdf5', borderColor: '#a7f3d0', color: '#065f46', marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      <div style={{ ...card, marginBottom: 12 }}>
        <StandardFilterBar value={f} onChange={setF} periodMode="range"
          show={{ period: true, stores: true, markets: true, reps: true }}
          optionsUrl="/api/v1/core/filter-options" />
      </div>

      {totals && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          {[['Employees', String(totals.employees)], ['Hours', String(totals.hours)],
            ...(canSeePay ? [['Payroll', money(totals.pay)], ['Ready to pay', money(totals.payable_pay)]] : []),
            ['Held', String(totals.held)]].map(([k, v]) => (
            <div key={k} style={{ ...card, padding: '10px 16px', minWidth: 120 }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '.06em', fontWeight: 700 }}>{k}</div>
              <div style={{ fontSize: 19, fontWeight: 700, marginTop: 2, color: k === 'Held' && totals.held > 0 ? '#b45309' : 'var(--text1)' }}>{v}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        {chips.map(([k, label, n]) => (
          <button key={k} onClick={() => setChip(k)} style={{ ...btn, fontSize: 12, padding: '4px 11px', borderRadius: 20,
            background: chip === k ? 'var(--accent)' : 'var(--surface)', color: chip === k ? '#fff' : 'var(--text2)',
            border: chip === k ? 'none' : '1px solid var(--border)' }}>{label} ({n})</button>
        ))}
        <div style={{ flex: 1 }} />
        <ReportExportBar title="Payroll — Hours Approval"
          subtitle={period
            ? `Pay period ${period.start} to ${period.end}${cycle?.payday ? ` · payable ${cycle.payday}` : ''}`
            : undefined}
          columns={cols} rows={shown} />
      </div>

      <div style={{ ...card, padding: 0, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ padding: '10px 14px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, color: 'var(--text2)', fontWeight: 600 }}>{selIds.length} selected</span>
          <button style={{ ...btnP, opacity: selIds.length && !busy ? 1 : 0.5 }} disabled={!selIds.length || busy}
            onClick={() => decide('dm', 'approve')}>✓ Approve as DM</button>
          <button style={{ ...btnP, opacity: selIds.length && !busy ? 1 : 0.5 }} disabled={!selIds.length || busy}
            onClick={() => decide('hr', 'approve')}>✓✓ Approve as HR</button>
          <button style={{ ...btn, opacity: selIds.length && !busy ? 1 : 0.5 }} disabled={!selIds.length || busy}
            onClick={() => decide('dm', 'send_back')}>↩ Send back</button>
          <select disabled={!selIds.length || busy} defaultValue=""
            onChange={e => { setPayer(e.target.value); e.currentTarget.value = '' }}
            style={{ ...btn, fontWeight: 500, opacity: selIds.length ? 1 : 0.5 }}>
            <option value="" disabled>Paid by…</option>
            {payers.map(p => <option key={p.id} value={p.id}>{p.name}{p.is_default ? ' (default)' : ''}</option>)}
          </select>
          {isAdmin && (
            <button style={{ ...btn, opacity: selIds.length && !busy ? 1 : 0.5, color: '#b45309' }}
              disabled={!selIds.length || busy} onClick={override}>⚠ Pay without approval</button>
          )}
          <div style={{ width: 1, height: 22, background: 'var(--border)' }} />
          <button style={{ ...btn, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={() => dispatch(true)}>Preview send</button>
          <button style={{ ...btnP, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={() => {
            if (window.confirm('Send each payer the employees they pay? Only fully-approved (or overridden) rows go.')) dispatch(false)
          }}>📤 Send to payers</button>
        </div>
      </div>

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th style={{ width: 34 }}>
                <input type="checkbox" checked={allShown}
                  onChange={e => setSel(e.target.checked ? Object.fromEntries(shown.map(r => [r.employee_id, true])) : {})} />
              </th>
              <th>Employee</th><th>Store</th>
              <th style={{ textAlign: 'right' }}>Sched</th>
              <th style={{ textAlign: 'right' }} title="Gross hours from the clock, before the lunch deduction">Worked</th>
              <th style={{ textAlign: 'right' }} title="Auto lunch deduction, already applied on the Payroll screen">Lunch</th>
              <th style={{ textAlign: 'right' }} title="Manual +/- correction. Needs a reason — it moves a payroll number">Adjustment</th>
              <th style={{ textAlign: 'right' }} title="Worked − lunch ± adjustment. This is the figure being approved">Payable</th>
              <th style={{ textAlign: 'right' }}>Approve hours</th>
              {canSeePay && <th style={{ textAlign: 'right' }}>Pay</th>}
              <th>DM</th><th>HR</th><th>Paid by</th><th>State</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr><td colSpan={15} style={{ color: 'var(--text3)', padding: 22, textAlign: 'center' }}>
                {ready ? 'Nobody worked in this week, or your filters exclude everyone.' : 'Not activated yet.'}
              </td></tr>
            )}
            {shown.map(r => {
              const e = edits[r.employee_id]
              const typed = e?.hours ?? ''
              const changed = typed !== '' && Number(typed) !== (r.hours_source ?? 0)
              const adjusted = (e?.adj ?? '') !== '' && Number(e?.adj) !== (r.adjustment_hours ?? 0)
              return (
                <tr key={r.employee_id} style={{ background: r.held ? '#fffbeb' : undefined }}>
                  <td><input type="checkbox" checked={!!sel[r.employee_id]}
                    onChange={ev => setSel(s => ({ ...s, [r.employee_id]: ev.target.checked }))} /></td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{r.name || r.employee_id}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.employee_id}</div>
                    {/* A 0.00 that means "the clock has nothing" must not look like a real zero. When
                        the POS proves they were working, say so — that is the correction to make. */}
                    {r.no_clock_record && (
                      <div style={{ fontSize: 10.5, color: '#b45309', marginTop: 2 }}>
                        {r.worked_days_evidence?.length
                          ? `no clock record — POS shows ${r.worked_days_evidence.length} day(s) worked`
                          : 'no clock record this period'}
                        {!!r.worked_days_evidence?.length && (
                          <div style={{ color: 'var(--text3)', fontSize: 10 }}>
                            {r.worked_days_evidence.slice(0, 7).join(', ')}
                            {r.worked_days_evidence.length > 7 ? ` +${r.worked_days_evidence.length - 7} more` : ''}
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                  <td>{r.store || <span style={{ color: 'var(--text3)' }}>—</span>}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.scheduled_hours ?? '—'}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {r.hours_worked ?? '—'}
                    {r.hours_drifted && (
                      <div style={{ fontSize: 10, color: '#b45309' }} title={`Was ${r.worked_at_approval} when this was approved`}>
                        changed since sign-off
                      </div>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: r.lunch_hours ? '#b45309' : 'var(--text3)' }}>
                    {r.lunch_hours ? `− ${r.lunch_hours.toFixed(2)}` : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <input value={e?.adj ?? ''} placeholder={String(r.adjustment_hours ?? 0)} inputMode="decimal"
                      onChange={ev => setEdit(r.employee_id, { adj: ev.target.value })}
                      title="+ adds hours, − takes them away"
                      style={{ width: 60, padding: '4px 6px', textAlign: 'right', borderRadius: 6, fontSize: 13,
                        border: `1px solid ${adjusted ? '#f59e0b' : 'var(--border)'}`, background: 'var(--surface)' }} />
                    {adjusted && (
                      <input value={e?.adjReason || ''} placeholder="reason (required)"
                        onChange={ev => setEdit(r.employee_id, { adjReason: ev.target.value })}
                        style={{ display: 'block', marginTop: 4, width: 150, padding: '4px 6px', fontSize: 11.5,
                          borderRadius: 6, border: '1px solid #f59e0b', background: 'var(--surface)' }} />
                    )}
                    {!adjusted && !!r.adjustment_hours && (
                      <div style={{ fontSize: 10.5, color: '#b45309' }} title={r.adjustment_reason || ''}>
                        {r.adjustment_reason ? r.adjustment_reason.slice(0, 24) : 'adjusted'}
                      </div>
                    )}
                  </td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 700 }}>
                    {(adjusted && Number.isFinite(Number(e?.adj))
                      ? (r.hours_source ?? 0) + Number(e?.adj)
                      : (r.hours_payable ?? 0)).toFixed(2)}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <input value={typed} placeholder={String(r.hours_effective ?? '')} inputMode="decimal"
                      onChange={ev => setEdit(r.employee_id, { hours: ev.target.value })}
                      style={{ width: 66, padding: '4px 6px', textAlign: 'right', borderRadius: 6, fontSize: 13,
                        border: `1px solid ${changed ? '#f59e0b' : 'var(--border)'}`, background: 'var(--surface)' }} />
                    {changed && (
                      <input value={e?.reason || ''} placeholder="reason (required)"
                        onChange={ev => setEdit(r.employee_id, { reason: ev.target.value })}
                        style={{ display: 'block', marginTop: 4, width: 150, padding: '4px 6px', fontSize: 11.5,
                          borderRadius: 6, border: '1px solid #f59e0b', background: 'var(--surface)' }} />
                    )}
                    {r.hours_corrected && !changed && (
                      <div style={{ fontSize: 10.5, color: '#b45309' }}>was {r.hours_source}</div>
                    )}
                  </td>
                  {canSeePay && <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(r.pay_effective)}</td>}
                  <td><Pill status={r.dm_status} />{r.dm_by && <div style={{ fontSize: 10, color: 'var(--text3)' }}>{r.dm_by}</div>}</td>
                  <td><Pill status={r.hr_status} />{r.hr_by && <div style={{ fontSize: 10, color: 'var(--text3)' }}>{r.hr_by}</div>}</td>
                  <td>
                    {r.payer_name
                      ? <><div>{r.payer_name}</div><div style={{ fontSize: 10, color: 'var(--text3)' }}>via {r.payer_from}</div></>
                      : <span style={{ color: '#b45309', fontSize: 12 }}>not routed</span>}
                  </td>
                  <td style={{ fontSize: 11.5 }}>
                    {r.dispatch_status === 'sent' && <span style={{ color: '#15803d', fontWeight: 600 }}>sent {(r.dispatched_at || '').slice(0, 10)}</span>}
                    {r.dispatch_status === 'failed' && <span style={{ color: '#991b1b', fontWeight: 600 }}>send failed</span>}
                    {r.dispatch_status !== 'sent' && r.dispatch_status !== 'failed' && r.held &&
                      <span style={{ color: '#b45309', fontWeight: 600 }}>held</span>}
                    {r.override_by && <div style={{ color: '#b45309' }}>override: {r.override_reason}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
