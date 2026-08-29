'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt, getActiveOrg, ORG_ID } from '@/lib/client'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'

// DAILY COMMISSION — accrued (expected) commission per rep per day, cash advanced against it, and the
// balance. The commission side of the Envelope Expense/Payout package (owner 2026-08-04), with the
// owner's follow-up answers of the same day (ledger Q14 / Q17 / Q18 / Q19) wired in.
//
// ⚠️ READ THIS BEFORE CHANGING A NUMBER ON THIS PAGE. Everything under "Accrued" is a PROBABLE
// (expected) figure — the same doctrine as the M2–M6 expected column. It is NOT a payslip, it is not
// what the rep is owed, and nothing on this page (or behind it) writes rep_commissions, a plan, a
// schedule or any payout figure. "Paid" rows are CASH ADVANCES recorded against the accrual; recording
// one moves paid/due and nothing else.
//
// HOW THE DAY IS TIERED (owner ledger Q18: "based on tier meeting on that day, it keeps varying
// throughout the month as their commission changes in the individual rep report"): by default each day
// is accrued at the tier the rep is MEETING — the month-to-date total is computed with real attainment
// and shared across the month's accrued days, so the days add up to the rep report's month-to-date
// number and the whole month restates when attainment moves. A tenant can switch back to the
// conservative un-tiered basis in Settings. Whatever is left over at month close still arrives once as
// the monthly TRUE-UP (for Boost that is always the KPI tier + trade-in spiff).
//
// BALANCES ARE PER CYCLE (ledger Q19): they reset each calendar month / payroll cycle / commission
// cycle as configured, an unsettled prior cycle stays visible as a labelled CARRY-OVER line, and the
// settlement checklist is ADVICE — nothing here settles or moves money.
//
// OVER-ADVANCE (ledger Q14): always flagged. With over_advance_mode='auto_net' a prior cycle's
// over-advance also reduces the next cash due — shown as its own labelled line, never silently.
//
// RULE THREE (pick-don't-type): the record-a-payout form picks the employee and the store from the
// values already on the page — never a free-text name.
// RULE FOUR: every table is a <ReportShell> (Excel / PDF / Print / Send by email + WhatsApp).
// RULE FIVE: one <StandardFilterBar> (date range · stores · markets · reps) drives the tiles, all
// tables AND their exports — what you see is what exports.

const orgParam = () => { const o = getActiveOrg(); return o || ORG_ID }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { flex: 1, minWidth: 160, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const tileCap: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.3 }
const tileVal: React.CSSProperties = { fontSize: 22, fontWeight: 700, marginTop: 4 }
const lbl: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.3, display: 'block', marginBottom: 3 }

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const today = () => iso(new Date())
const firstOfMonth = () => { const d = new Date(); return iso(new Date(d.getFullYear(), d.getMonth(), 1)) }

const EMP_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.name, role: 'rep' },
  { header: 'Store(s)', get: r => (r.store_codes || []).join(', '), role: 'store' },
  { header: 'Market', get: r => (r.markets || []).join(', ') },
  { header: 'Accrued this cycle (expected)', get: r => r.accrued_total, money: true },
  { header: '— daily base', get: r => r.components?.base, money: true },
  { header: '— tier / true-up', get: r => r.components?.tier, money: true },
  { header: 'Advanced this cycle', get: r => r.paid_total, money: true },
  { header: 'Cycle balance', get: r => r.unpaid_balance, money: true },
  { header: 'Carry-over (prior cycles)', get: r => r.carry_over, money: true },
  { header: 'Auto-net applied', get: r => r.net_applied, money: true },
  { header: 'Due now (cash)', get: r => r.due_now, money: true },
  { header: 'Accrued today', get: r => r.today_accrual, money: true },
  { header: 'Days accrued', get: r => r.accrual_days, type: 'number' },
  { header: 'Last advance', get: r => r.last_paid_date, type: 'date' },
  { header: 'Flag', get: r => (r.over_advanced ? 'OVER-ADVANCED' : r.carry_over < 0 ? 'PRIOR OVER-ADVANCE' : '') },
]

const DAY_COLS: ExportColumn[] = [
  { header: 'Date', get: r => r.work_date, type: 'date' },
  { header: 'Rep', get: r => r.name, role: 'rep' },
  { header: 'Store', get: r => r.store_code, role: 'store' },
  { header: 'Market', get: r => r.market },
  { header: 'Daily accrual', get: r => r.base_amount, money: true },
  { header: 'Tier / true-up', get: r => r.tier_amount, money: true },
  { header: 'Total', get: r => r.total_amount, money: true },
  { header: 'How it was computed', get: r => describe(r) },
]

const LEDGER_COLS: ExportColumn[] = [
  { header: 'Paid date', get: r => r.paid_date, type: 'date' },
  { header: 'Rep', get: r => r.name, role: 'rep' },
  { header: 'Store', get: r => r.store_code, role: 'store' },
  { header: 'Amount', get: r => r.amount, money: true },
  { header: 'Method', get: r => r.method },
  { header: 'Envelope withdrawal', get: r => r.withdrawal_ref },
  { header: 'Note', get: r => r.note },
  { header: 'Recorded by', get: r => r.recorded_by },
]

const SETTLE_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.name, role: 'rep' },
  { header: 'Store(s)', get: r => (r.store_codes || []).join(', '), role: 'store' },
  { header: 'Cycle', get: r => r.cycle_label },
  { header: 'Accrued this cycle', get: r => r.cycle_accrued, money: true },
  { header: 'Cash advanced', get: r => r.cycle_advanced, money: true },
  { header: 'Remainder this cycle', get: r => r.cycle_remainder, money: true },
  { header: 'Carry-over (unsettled)', get: r => r.carry_over, money: true },
  { header: 'To pay in cash', get: r => r.to_pay, money: true },
  { header: 'To collect back', get: r => r.to_collect, money: true },
  { header: 'Status', get: r => r.status },
]

/** One-line plain-language explanation of an accrual row, from its own `components` blob. */
function describe(r: any): string {
  const c = r?.components || {}
  const bits: string[] = []
  if (c.mode === 'plan') {
    const rules = (c.rules || []).filter((x: any) => Number(x.payout) !== 0)
      .map((x: any) => `${x.label}: ${fmt(x.payout)}`)
    if (c.plan_name) bits.push(`plan “${c.plan_name}”`)
    if (rules.length) bits.push(rules.join(' · '))
    if (Number(c.setup_fee_comm)) bits.push(`set-up fee ${fmt(c.setup_fee_comm)}`)
  } else if (c.mode === 'boost') {
    const named: [string, string][] = [
      ['premium_comm', 'premium'], ['byod_comm', 'BYOD'], ['upgrade_comm', 'upgrade'],
      ['acc_comm', 'accessories'], ['setup_fee_comm', 'set-up fee'], ['acima_comm', 'ACIMA'],
      ['custom_comm', 'custom'],
    ]
    const parts = named.filter(([k]) => Number(c[k])).map(([k, lbl2]) => `${lbl2}: ${fmt(c[k])}`)
    if (parts.length) bits.push(parts.join(' · '))
  } else if (c.mode === 'tier_only') {
    bits.push('no sales this day')
  } else if (c.mode === 'mtd_only') {
    bits.push('no accrued day carried this rep’s weight — the whole month-to-date figure sits here')
  }
  if (c.mtd) {
    bits.push(`month-to-date ${fmt(c.mtd.mtd_total)} at the tier being met` +
      (c.mtd.factor ? ` (×${Number(c.mtd.factor).toFixed(3)} on ${fmt(c.mtd.untiered_base)} un-tiered for this day)` : ''))
  }
  if (c.tier) {
    bits.push(`monthly true-up for ${c.tier.source_period} (${fmt(c.tier.final_month_total)} run − ${fmt(c.tier.daily_base_accrued)} accrued over ${c.tier.days_accrued} day(s))`)
  }
  if ((c.deferred_to_monthly || []).length) bits.push(`deferred to the monthly true-up: ${(c.deferred_to_monthly).join(', ')}`)
  return bits.join(' — ') || '—'
}

export default function DailyCommissionPage() {
  const [filt, setFilt] = useState<StandardFilterValue>({ ...emptyStandardFilter(firstOfMonth()), periodTo: today() })
  const [acc, setAcc] = useState<any>(null)
  const [days, setDays] = useState<any>(null)
  const [ledger, setLedger] = useState<any>(null)
  const [over, setOver] = useState<any>(null)
  const [settle, setSettle] = useState<any>(null)
  const [cfg, setCfg] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [drill, setDrill] = useState<any>(null)
  const [showSettle, setShowSettle] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  // record-a-payout form (RULE THREE: employee + store are PICKED from what's on the page)
  const [pEmp, setPEmp] = useState<string | null>(null)
  const [pStore, setPStore] = useState<string | null>(null)
  const [pAmt, setPAmt] = useState('')
  const [pDate, setPDate] = useState(today())
  const [pNote, setPNote] = useState('')
  const [pRef, setPRef] = useState('')
  const [saving, setSaving] = useState(false)

  // run-a-date control (idempotent; recomputes ONE date's expected numbers, never a month)
  const [runDate, setRunDate] = useState(today())
  const [running, setRunning] = useState(false)

  // settings draft (RULE TWO: every knob is tenant config, edited here, saved through the config API)
  const [draft, setDraft] = useState<any>(null)
  const [savingCfg, setSavingCfg] = useState(false)

  const from = filt.period || firstOfMonth()
  const to = filt.periodTo || today()

  const load = useCallback(() => {
    setBusy(true); setErr('')
    const org = orgParam()
    const q = (p: string) => `${p}${p.includes('?') ? '&' : '?'}org_id=${encodeURIComponent(org)}`
    Promise.all([
      api(q(`/api/v1/commcalc/payout/accrued?as_of=${to}`)),
      api(q(`/api/v1/commcalc/payout/accrual?start=${from}&end=${to}`)),
      api(q(`/api/v1/commcalc/payout/ledger?start=${from}&end=${to}`)),
      api(q(`/api/v1/commcalc/payout/over-advance?as_of=${to}`)),
      api(q(`/api/v1/commcalc/payout/settlement?as_of=${to}`)),
      api(q(`/api/v1/commcalc/payout/accrual/config?as_of=${to}`)),
    ]).then(([a, d, l, o, s, c]) => {
      setAcc(a); setDays(d); setLedger(l); setOver(o); setSettle(s); setCfg(c)
      setDraft((prev: any) => prev || JSON.parse(JSON.stringify(c?.config || {})))
    })
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [from, to])

  useEffect(() => { load() }, [load])

  const empRows = acc?.employees || []
  // Store/market are multi-valued per employee, so membership (not first-value) decides — a rep who
  // sold in two stores must survive a filter on either of them.
  const matchEmp = useCallback((r: any) => {
    const f = filt
    if (f.reps.length && !f.reps.some(x => String(x).toLowerCase() === String(r.name || '').toLowerCase())) return false
    if (f.stores.length && !(r.store_codes || []).some((c: string) => f.stores.some(x => String(x).toLowerCase() === String(c).toLowerCase()))) return false
    if (f.markets.length && !(r.markets || []).some((c: string) => f.markets.some(x => String(x).toLowerCase() === String(c).toLowerCase()))) return false
    return true
  }, [filt])
  const shownEmps = useMemo(() => empRows.filter(matchEmp), [empRows, matchEmp])
  const shownSettle = useMemo(() => (settle?.employees || []).filter(matchEmp), [settle, matchEmp])

  const dayAcc = useMemo(() => ({
    rep: (r: any) => r.name, store: (r: any) => r.store_code,
    market: (r: any) => r.market, date: (r: any) => r.work_date,
  }), [])
  const shownDays = useMemo(() => filterRows(days?.rows || [], filt, dayAcc), [days, filt, dayAcc])
  const ledgerAcc = useMemo(() => ({
    rep: (r: any) => r.name, store: (r: any) => r.store_code, date: (r: any) => r.paid_date,
  }), [])
  const shownLedger = useMemo(() => filterRows(ledger?.rows || [], filt, ledgerAcc), [ledger, filt, ledgerAcc])

  // Filter options come from the org-scoped rows already on the page (never a hard-coded list).
  const storeOpts = useMemo<EntityOption[]>(() => {
    const s = new Map<string, string>()
    ;(days?.rows || []).forEach((r: any) => { if (r.store_code) s.set(String(r.store_code).toLowerCase(), r.store_code) })
    empRows.forEach((r: any) => (r.store_codes || []).forEach((c: string) => c && s.set(String(c).toLowerCase(), c)))
    return [...s.values()].sort().map(x => ({ id: x, label: x }))
  }, [days, empRows])
  const marketOpts = useMemo<EntityOption[]>(() => {
    const s = new Set<string>()
    ;(days?.rows || []).forEach((r: any) => { if (r.market) s.add(r.market) })
    empRows.forEach((r: any) => (r.markets || []).forEach((m: string) => m && s.add(m)))
    return [...s].sort().map(x => ({ id: x, label: x }))
  }, [days, empRows])
  const repOpts = useMemo<EntityOption[]>(() => {
    const s = new Map<string, string>()
    empRows.forEach((r: any) => s.set(String(r.name).toLowerCase(), r.name))
    ;(days?.rows || []).forEach((r: any) => { if (r.name) s.set(String(r.name).toLowerCase(), r.name) })
    return [...s.values()].sort().map(x => ({ id: x, label: x }))
  }, [empRows, days])
  // The payout form picks a PERSON (label) but stores their canonical employee_key — never the string.
  const payeeOpts = useMemo<EntityOption[]>(() => empRows.map((r: any) => ({
    id: r.employee_key, label: r.name,
    sublabel: `due now ${fmt(r.due_now)}${(r.store_codes || []).length ? ` · ${(r.store_codes).join(', ')}` : ''}`,
  })), [empRows])

  const shownTotals = useMemo(() => ({
    accrued: shownEmps.reduce((s: number, r: any) => s + Number(r.accrued_total || 0), 0),
    paid: shownEmps.reduce((s: number, r: any) => s + Number(r.paid_total || 0), 0),
    unpaid: shownEmps.reduce((s: number, r: any) => s + Number(r.unpaid_balance || 0), 0),
    due: shownEmps.reduce((s: number, r: any) => s + Number(r.due_now || 0), 0),
    carry: shownEmps.reduce((s: number, r: any) => s + Number(r.carry_over || 0), 0),
    today: shownEmps.reduce((s: number, r: any) => s + Number(r.today_accrual || 0), 0),
    flagged: shownEmps.filter((r: any) => r.over_advanced || Number(r.carry_over) < 0).length,
  }), [shownEmps])

  const notReady = acc && acc.ready === false
  const cycle = acc?.cycle || cfg?.cycle
  const canRecord = cfg?.can_record !== false

  const record = () => {
    if (!pEmp || !pAmt) { setMsg('Pick a rep and enter an amount.'); return }
    const chosen = empRows.find((r: any) => r.employee_key === pEmp)
    setSaving(true); setMsg('')
    api(`/api/v1/commcalc/payout/record?org_id=${encodeURIComponent(orgParam())}`, {
      method: 'POST',
      body: JSON.stringify({
        employee_key: pEmp, employee_name: chosen?.name || undefined,
        amount: Number(pAmt), paid_date: pDate, store_code: pStore || undefined,
        withdrawal_ref: pRef.trim() || undefined, note: pNote.trim() || undefined,
        method: 'envelope_cash',
      }),
    }).then((r: any) => {
      setMsg(r?.duplicate
        ? 'That envelope withdrawal was already recorded — nothing was added.'
        : r?.ready === false ? (r.note || 'Not set up yet.')
        : `Recorded ${fmt(Number(pAmt))} to ${chosen?.name || pEmp}. This is a cash advance — it changes advanced/due only.`)
      setPAmt(''); setPRef(''); setPNote('')
      load()
    }).catch(e => setMsg(String(e?.message || e))).finally(() => setSaving(false))
  }

  const runOne = () => {
    setRunning(true); setMsg('')
    api(`/api/v1/commcalc/payout/accrual/run?org_id=${encodeURIComponent(orgParam())}&date=${runDate}`, { method: 'POST' })
      .then((r: any) => {
        setMsg(r?.ready === false ? (r.note || 'Not set up yet.')
          : `${runDate}: ${r.employees || 0} rep(s), ${fmt(r.base_total || 0)} accrued` +
            (r.restated ? `, ${r.restated} earlier day(s) restated to the tier now being met` : '') +
            (r.tier_recognitions ? `, ${r.tier_recognitions} monthly true-up(s) recognized (${fmt(r.tier_recognized || 0)})` : '') +
            `. Re-running the same date restates it — it never adds twice.`)
        load()
      })
      .catch(e => setMsg(String(e?.message || e))).finally(() => setRunning(false))
  }

  const setDraftPath = (path: string[], v: any) => setDraft((d: any) => {
    const next = JSON.parse(JSON.stringify(d || {}))
    let cur = next
    path.slice(0, -1).forEach(k => { cur[k] = cur[k] || {}; cur = cur[k] })
    cur[path[path.length - 1]] = v
    return next
  })

  const saveCfg = () => {
    setSavingCfg(true); setMsg('')
    api(`/api/v1/commcalc/payout/accrual/config?org_id=${encodeURIComponent(orgParam())}`, {
      method: 'PUT', body: JSON.stringify(draft || {}),
    }).then(() => {
      setMsg('Settings saved. These change how the expected numbers are SHOWN — no payout was touched. ' +
        'Recompute a day (or wait for tonight’s run) to restate the current month on the new basis.')
      load()
    }).catch(e => setMsg(String(e?.message || e))).finally(() => setSavingCfg(false))
  }

  const basis = draft?.tier_basis || cfg?.config?.tier_basis
  const cycleMode = draft?.cycle?.mode || 'calendar_month'

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Daily Incentive</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', lineHeight: 1.6 }}>
          What each rep has <b>probably</b> earned this cycle, what has been <b>advanced to them in cash</b>,
          and what is due now. <b>Accrued is an expected figure, not a payslip</b> — it never changes anyone's
          pay, and a recorded advance never reduces what they are owed at month end. Each day is accrued at
          the <b>tier the rep is currently meeting</b>, so the days add up to the rep report's month-to-date
          number and the whole month restates as attainment moves.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt} periodMode="range"
          show={{ period: true, stores: true, markets: true, reps: true }}
          storeOptions={storeOpts} marketOptions={marketOpts} repOptions={repOpts}
          storeLabel="Stores…" marketLabel="Markets…" repLabel="Reps…"
          right={<div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-secondary" onClick={() => setShowSettings(s => !s)}>
              {showSettings ? 'Hide settings' : '⚙ Settings'}
            </button>
            <button className="btn btn-secondary" onClick={load} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>
          </div>}
        />
      </div>

      {err && <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13 }}>{err}</div>}
      {notReady && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', marginBottom: 14, fontSize: 13, lineHeight: 1.7 }}>
          <b>Daily incentive accrual is not switched on yet.</b><br />{acc.note}
        </div>
      )}

      {/* ── settings (RULE TWO: every knob is tenant config) ───────────────────────────────── */}
      {showSettings && cfg && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Accrual settings</div>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 12, lineHeight: 1.6 }}>
            These are this tenant's settings — they change how the <b>expected</b> numbers are computed and
            shown. They never change what anybody is paid, and saving them writes nothing to any payout.
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <label style={lbl}>Tier basis</label>
              <select style={sel} value={basis || ''} onChange={e => setDraftPath(['tier_basis'], e.target.value)}>
                {(cfg.tier_basis_options || []).map((o: string) => (
                  <option key={o} value={o}>{o === 'mtd_attained' ? 'Tier being met (month-to-date)'
                    : o === 'none' ? 'Un-tiered day (conservative)' : 'The day’s own tier'}</option>
                ))}
              </select>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', maxWidth: 320, marginTop: 4, lineHeight: 1.5 }}>
                {cfg.explain?.tier_basis?.[basis]}
              </div>
            </div>
            <div>
              <label style={lbl}>Over-advance</label>
              <select style={sel} value={draft?.over_advance_mode || 'flag'}
                onChange={e => setDraftPath(['over_advance_mode'], e.target.value)}>
                {(cfg.over_advance_modes || []).map((o: string) => (
                  <option key={o} value={o}>{o === 'flag' ? 'Flag only (default)' : 'Flag and auto-net'}</option>
                ))}
              </select>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', maxWidth: 320, marginTop: 4, lineHeight: 1.5 }}>
                {cfg.explain?.over_advance_mode?.[draft?.over_advance_mode || 'flag']}
              </div>
            </div>
            <div>
              <label style={lbl}>Balance cycle</label>
              <select style={sel} value={cycleMode} onChange={e => setDraftPath(['cycle', 'mode'], e.target.value)}>
                {(cfg.cycle_modes || []).map((o: string) => (
                  <option key={o} value={o}>{o === 'calendar_month' ? 'Calendar month'
                    : o === 'payroll' ? 'Payroll cycle' : 'Incentive cycle'}</option>
                ))}
              </select>
            </div>
            {cycleMode === 'payroll' && (
              <>
                <div>
                  <label style={lbl}>Payroll cycle</label>
                  <select style={sel} value={draft?.cycle?.payroll?.kind || 'semimonthly'}
                    onChange={e => setDraftPath(['cycle', 'payroll', 'kind'], e.target.value)}>
                    {(cfg.payroll_kinds || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                {(draft?.cycle?.payroll?.kind || 'semimonthly') === 'semimonthly' ? (
                  <div>
                    <label style={lbl}>Second half starts on</label>
                    <input style={{ ...sel, width: 90 }} type="number" min={2} max={28}
                      value={draft?.cycle?.payroll?.semi_day ?? 16}
                      onChange={e => setDraftPath(['cycle', 'payroll', 'semi_day'], Number(e.target.value))} />
                  </div>
                ) : (
                  <div>
                    <label style={lbl}>Anchor date</label>
                    <input style={sel} type="date" value={draft?.cycle?.payroll?.anchor_date || ''}
                      onChange={e => setDraftPath(['cycle', 'payroll', 'anchor_date'], e.target.value)} />
                  </div>
                )}
              </>
            )}
            {cycleMode === 'commission' && (
              <div>
                <label style={lbl}>Cycle closes on day</label>
                <input style={{ ...sel, width: 90 }} type="number" min={1} max={31}
                  value={draft?.cycle?.commission?.end_day ?? ''}
                  placeholder="month end"
                  onChange={e => setDraftPath(['cycle', 'commission', 'end_day'], e.target.value ? Number(e.target.value) : null)} />
              </div>
            )}
            <div>
              <label style={lbl}>Advise settling</label>
              <input style={{ ...sel, width: 110 }} type="number" min={0} max={28}
                value={draft?.cycle?.settlement_advice_days ?? 3}
                onChange={e => setDraftPath(['cycle', 'settlement_advice_days'], Number(e.target.value))} />
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 4 }}>days before the cycle ends</div>
            </div>
            <div>
              <label style={lbl}>True-up recognition</label>
              <select style={sel} value={draft?.tier_recognition?.mode || 'on_run_available'}
                onChange={e => setDraftPath(['tier_recognition', 'mode'], e.target.value)}>
                {(cfg.recognition_modes || []).map((o: string) => (
                  <option key={o} value={o}>{o === 'on_run_available' ? 'As soon as the run exists' : 'On a day of the month'}</option>
                ))}
              </select>
            </div>
            {draft?.tier_recognition?.mode === 'day_of_month' && (
              <div>
                <label style={lbl}>Recognize on day</label>
                <input style={{ ...sel, width: 90 }} type="number" min={1} max={31}
                  value={draft?.tier_recognition?.day_of_month ?? 1}
                  onChange={e => setDraftPath(['tier_recognition', 'day_of_month'], Number(e.target.value))} />
              </div>
            )}
            <div>
              <label style={lbl}>Daily auto-run</label>
              <select style={sel} value={draft?.auto_run?.enabled === false ? 'off' : 'on'}
                onChange={e => setDraftPath(['auto_run', 'enabled'], e.target.value === 'on')}>
                <option value="on">On</option><option value="off">Off</option>
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button className="btn btn-primary" onClick={saveCfg} disabled={savingCfg}>
                {savingCfg ? 'Saving…' : 'Save settings'}
              </button>
            </div>
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10 }}>
            Who may record a cash advance: <b>{(cfg.config?.record_roles || []).join(', ')}</b> (district
            manager or above, per the owner's rule) — plus any role whose scope spans a market or the whole org.
          </div>
        </div>
      )}

      {acc?.ready && (
        <>
          {/* ── cycle banner + settle advisory (ledger Q19) ──────────────────────────────── */}
          <div className="card" style={{
            marginBottom: 14, borderLeft: `4px solid ${acc.settlement_advisory?.due ? 'var(--amber, #b45309)' : 'var(--border)'}`,
            display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap',
          }}>
            <div style={{ fontSize: 13, lineHeight: 1.6 }}>
              <b>Cycle: {cycle?.label}</b> ({cycle?.start} → {cycle?.end}
              {typeof cycle?.days_left === 'number' ? `, ${cycle.days_left} day(s) left` : ''}).
              Balances reset each cycle; anything unsettled from an earlier cycle is shown as a
              labelled carry-over line and never rolled in silently.
              {acc.settlement_advisory?.message ? <> <b>{acc.settlement_advisory.message}</b></> : null}
            </div>
            <button className="btn btn-secondary" onClick={() => setShowSettle(s => !s)}>
              {showSettle ? 'Hide settlement checklist' : 'Settle employee balances'}
            </button>
          </div>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <div className="card" style={tile}>
              <div style={tileCap}>Accrued this cycle</div>
              <div style={tileVal}>{fmt(shownTotals.accrued)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>expected — never paid from this page</div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Advanced this cycle</div>
              <div style={tileVal}>{fmt(shownTotals.paid)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>cash from the envelope</div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Due now (cash)</div>
              <div style={{ ...tileVal, color: shownTotals.due < 0 ? 'var(--red)' : 'var(--text)' }}>{fmt(shownTotals.due)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                {acc.over_advance_mode === 'auto_net' ? 'net of prior over-advances' : 'this cycle only'}
              </div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Carry-over</div>
              <div style={{ ...tileVal, color: shownTotals.carry < 0 ? 'var(--red)' : 'var(--text)' }}>{fmt(shownTotals.carry)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>unsettled from earlier cycles</div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Accrued on {to}</div>
              <div style={tileVal}>{fmt(shownTotals.today)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>that day's sales</div>
            </div>
            <div className="card" style={{ ...tile, borderColor: shownTotals.flagged ? 'var(--red)' : 'var(--border)' }}>
              <div style={tileCap}>Over-advanced</div>
              <div style={{ ...tileVal, color: shownTotals.flagged ? 'var(--red)' : 'var(--text)' }}>{shownTotals.flagged}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                {acc.over_advance_mode === 'auto_net' ? 'flagged + auto-netted next cycle' : 'flag only — nothing is clawed back'}
              </div>
            </div>
          </div>

          {/* ── settlement checklist (advisory only) ─────────────────────────────────────── */}
          {showSettle && (
            <div style={{ marginBottom: 14 }}>
              <ReportShell
                title={`Settle employee balances — ${settle?.cycle?.label || cycle?.label}`}
                subtitle={settle?.advisory?.message || 'advisory only — nothing here settles or moves money'}
                filename={`commission-settlement-${to}`}
                columns={SETTLE_COLS} rows={shownSettle} totals compact stickyHeader
                rowStyle={(r: any) => (r.status === 'settled' ? undefined
                  : r.carry_over < 0 ? { background: '#fef2f2' } : { background: '#fffbeb' })}
              />
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6, lineHeight: 1.6 }}>
                {settle?.note}
              </div>
            </div>
          )}

          {/* ── over-advance review ─────────────────────────────────────────────────────── */}
          {(over?.running?.length || over?.cycle?.length || over?.monthly?.length) ? (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--red)' }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>⚠️ Advances that have outrun the accrual</div>
              <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>
                {over.policy}
              </div>
              <table style={{ width: '100%', fontSize: 12.5 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Rep', 'What happened', 'Over by'].map(h => <th key={h} style={{ textAlign: 'left', padding: '4px 6px', color: 'var(--text2)' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {[...(over.cycle || []), ...(over.running || []), ...(over.monthly || [])].map((r: any, i: number) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px', fontWeight: 600 }}>{r.name}</td>
                      <td style={{ padding: '4px 6px', color: 'var(--text2)' }}>{r.reason}</td>
                      <td style={{ padding: '4px 6px', color: 'var(--red)', fontWeight: 700 }}>{fmt(r.over_by)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {/* ── per rep ─────────────────────────────────────────────────────────────────── */}
          <div style={{ marginBottom: 14 }}>
            <ReportShell
              title="Per rep — accrued vs advanced"
              subtitle={`cycle ${cycle?.label} · as of ${to} · accrued is EXPECTED incentive, advances are cash out of the envelope`}
              filename={`daily-commission-by-rep-${to}`}
              columns={EMP_COLS} rows={shownEmps} totals compact stickyHeader
              onRowClick={(r: any) => setDrill({ ...r, __lines: true })}
              rowStyle={(r: any) => (r.over_advanced ? { background: '#fef2f2' } : undefined)}
            />
          </div>

          {drill?.__lines && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--blue, #2563eb)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontWeight: 700 }}>{drill.name} — how “due now” is built</div>
                <button className="btn btn-secondary" onClick={() => setDrill(null)}>Close</button>
              </div>
              <table style={{ width: '100%', fontSize: 12.5, marginTop: 8 }}>
                <tbody>
                  {(drill.lines || []).map((l: any, i: number) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '5px 6px', color: l.kind === 'due' ? 'var(--text)' : 'var(--text2)', fontWeight: l.kind === 'due' ? 700 : 400 }}>{l.label}</td>
                      <td style={{ padding: '5px 6px', textAlign: 'right', fontWeight: l.kind === 'due' ? 700 : 600, color: Number(l.amount) < 0 ? 'var(--red)' : 'var(--text)' }}>{fmt(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8, lineHeight: 1.6 }}>
                Lifetime: accrued {fmt(drill.lifetime_accrued)} · advanced {fmt(drill.lifetime_paid)} ·
                balance {fmt(drill.lifetime_balance)}. Balances shown above are for the current cycle only.
              </div>
            </div>
          )}

          {/* ── record an advance ───────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>Record a cash advance</div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10, lineHeight: 1.6 }}>
              Records money physically handed to a rep out of an envelope. It changes <b>advanced</b> and
              <b> due now</b> only — it does not pay anybody through payroll and does not change the accrual.
              {canRecord ? null : <> <b style={{ color: 'var(--red)' }}>{cfg?.can_record_reason}</b></>}
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <EntityPicker options={payeeOpts} value={pEmp} onChange={setPEmp} placeholder="Rep…" width={240} ariaLabel="Rep" />
              <EntityPicker options={storeOpts} value={pStore} onChange={setPStore} placeholder="Store…" width={160} ariaLabel="Store" />
              <input style={{ ...sel, width: 120 }} type="number" step="0.01" min="0" placeholder="Amount"
                value={pAmt} onChange={e => setPAmt(e.target.value)} aria-label="Amount" />
              <input style={sel} type="date" value={pDate} onChange={e => setPDate(e.target.value)} aria-label="Paid date" />
              <input style={{ ...sel, width: 190 }} placeholder="Envelope withdrawal ref (optional)"
                value={pRef} onChange={e => setPRef(e.target.value)} aria-label="Envelope withdrawal reference" />
              <input style={{ ...sel, width: 190 }} placeholder="Note (optional)"
                value={pNote} onChange={e => setPNote(e.target.value)} aria-label="Note" />
              <button className="btn btn-primary" onClick={record} disabled={saving || !pEmp || !pAmt || !canRecord}>
                {saving ? 'Recording…' : 'Record advance'}
              </button>
            </div>
            {msg && <div style={{ fontSize: 12.5, marginTop: 10, color: 'var(--text2)' }}>{msg}</div>}
          </div>

          {/* ── per day ─────────────────────────────────────────────────────────────────── */}
          <div style={{ marginBottom: 14 }}>
            <ReportShell
              title={`Per day — ${from} to ${to}`}
              subtitle="click a row for the full breakdown of how that day's number was computed"
              filename={`daily-commission-days-${from}-${to}`}
              columns={DAY_COLS} rows={shownDays} totals compact stickyHeader
              onRowClick={(r: any) => setDrill(r)}
              rowStyle={(r: any) => (Number(r.tier_amount) ? { background: '#f0f9ff' } : undefined)}
            />
          </div>

          {drill && !drill.__lines && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--blue, #2563eb)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontWeight: 700 }}>{drill.name} — {drill.work_date} — {fmt(drill.total_amount)}</div>
                <button className="btn btn-secondary" onClick={() => setDrill(null)}>Close</button>
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 8, lineHeight: 1.7 }}>
                {drill.components?.explain}
              </div>
              {drill.components?.mtd && (
                <div style={{ fontSize: 12.5, marginTop: 8, lineHeight: 1.7, background: 'var(--surface2)', padding: 10, borderRadius: 8 }}>
                  {drill.components.mtd.explain}
                  {drill.components.mtd.no_daily_weights && (
                    <div style={{ color: 'var(--amber, #b45309)', marginTop: 6 }}>
                      Heads-up: no accrued day carries this rep's weight yet, so the whole month-to-date
                      figure is sitting on this one row. Recompute the month's days to spread it.
                    </div>
                  )}
                </div>
              )}
              {drill.components?.tier && (
                <div style={{ fontSize: 12.5, marginTop: 8, lineHeight: 1.7, background: 'var(--surface2)', padding: 10, borderRadius: 8 }}>
                  {drill.components.tier.explain}
                  {drill.components.tier.partial_month_warning && (
                    <div style={{ color: 'var(--amber, #b45309)', marginTop: 6 }}>
                      Heads-up: only {drill.components.tier.days_accrued} day(s) of that month were accrued
                      daily, so most of this true-up is simply the part of the month the daily accrual
                      wasn't running for — not a bonus.
                    </div>
                  )}
                </div>
              )}
              {(drill.components?.rules || []).length > 0 && (
                <table style={{ width: '100%', fontSize: 12.5, marginTop: 10 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Rule', 'Pays', 'Lines matched', 'Tiered', 'Amount (un-tiered)'].map(h =>
                      <th key={h} style={{ textAlign: 'left', padding: '4px 6px', color: 'var(--text2)' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {drill.components.rules.map((rb: any, i: number) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '4px 6px' }}>{rb.label}</td>
                        <td style={{ padding: '4px 6px', color: 'var(--text3)' }}>{rb.payout_kind}</td>
                        <td style={{ padding: '4px 6px' }}>{rb.matched_lines}</td>
                        <td style={{ padding: '4px 6px', color: 'var(--text3)' }}>{rb.tiered ? 'yes' : 'no'}</td>
                        <td style={{ padding: '4px 6px', fontWeight: 600 }}>{fmt(rb.payout)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 10 }}>
                Read from <b>{drill.components?.source_table || '—'}</b>. Recomputed at {String(drill.computed_at || '').slice(0, 19).replace('T', ' ')}.
              </div>
            </div>
          )}

          {/* ── advances ledger ─────────────────────────────────────────────────────────── */}
          <div style={{ marginBottom: 14 }}>
            <ReportShell
              title={`Cash advances — ${from} to ${to}`}
              subtitle="every payout recorded against accrued incentive (append-only; corrections are their own decision)"
              filename={`commission-advances-${from}-${to}`}
              columns={LEDGER_COLS} rows={shownLedger} totals compact stickyHeader
            />
          </div>

          {/* ── recompute a date ────────────────────────────────────────────────────────── */}
          <div className="card">
            <div style={{ fontWeight: 700, marginBottom: 4 }}>Recompute a day</div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10, lineHeight: 1.6 }}>
              The accrual runs automatically every day, right after the sales feed lands. This button
              re-runs one date by hand — useful when a day's sales arrived late. It is <b>idempotent</b>:
              re-running a date restates it (and re-allocates the rest of that month to the tier now being
              met), never adds to it, and it is <b>not</b> an incentive recalculation — no payout is touched.
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <input style={sel} type="date" value={runDate} onChange={e => setRunDate(e.target.value)} aria-label="Date to recompute" />
              <button className="btn btn-secondary" onClick={runOne} disabled={running}>
                {running ? 'Recomputing…' : 'Recompute this day'}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
