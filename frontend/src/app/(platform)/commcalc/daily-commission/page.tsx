'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt, getActiveOrg, ORG_ID } from '@/lib/client'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'

// DAILY COMMISSION — accrued (expected) commission per rep per day, cash advanced against it, and the
// unpaid balance. The commission side of the Envelope Expense/Payout package (owner 2026-08-04).
//
// ⚠️ READ THIS BEFORE CHANGING A NUMBER ON THIS PAGE. Everything under "Accrued" is a PROBABLE
// (expected) figure — the same doctrine as the M2–M6 expected column. It is NOT a payslip, it is not
// what the rep is owed, and nothing on this page (or behind it) writes rep_commissions, a plan, a
// schedule or any payout figure. "Paid" rows are CASH ADVANCES recorded against the accrual; recording
// one moves paid/unpaid and nothing else. There is deliberately NO netting and NO clawback — where
// advances outrun the accrual the page FLAGS it for a human (ledger Q14 default).
//
// WHY THE DAY IS UN-TIERED: a single day cannot know a MONTHLY tier attainment, so the daily number is
// the day's own sale-derived commission at multiplier 1.0. The whole tier effect arrives once, later,
// as the monthly TRUE-UP (that month's finished run minus what was accrued daily) — which is why a
// rep's row can show a large one-off "Tier / true-up" amount, and why it can be negative. Every row's
// breakdown says so in words; click a row to see it.
//
// RULE THREE (pick-don't-type): the record-a-payout form picks the employee and the store from the
// values already on the page — never a free-text name.
// RULE FOUR: every table is a <ReportShell> (Excel / PDF / Print / Send by email + WhatsApp).
// RULE FIVE: one <StandardFilterBar> (date range · stores · markets · reps) drives the tiles, all
// three tables AND their exports — what you see is what exports.

const orgParam = () => { const o = getActiveOrg(); return o || ORG_ID }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { flex: 1, minWidth: 160, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const tileCap: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.3 }
const tileVal: React.CSSProperties = { fontSize: 22, fontWeight: 700, marginTop: 4 }

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const today = () => iso(new Date())
const firstOfMonth = () => { const d = new Date(); return iso(new Date(d.getFullYear(), d.getMonth(), 1)) }

const EMP_COLS: ExportColumn[] = [
  { header: 'Rep', get: r => r.name, role: 'rep' },
  { header: 'Store(s)', get: r => (r.store_codes || []).join(', '), role: 'store' },
  { header: 'Market', get: r => (r.markets || []).join(', ') },
  { header: 'Accrued (expected)', get: r => r.accrued_total, money: true },
  { header: '— daily base', get: r => r.components?.base, money: true },
  { header: '— tier / true-up', get: r => r.components?.tier, money: true },
  { header: 'Paid (advances)', get: r => r.paid_total, money: true },
  { header: 'Unpaid balance', get: r => r.unpaid_balance, money: true },
  { header: 'Accrued today', get: r => r.today_accrual, money: true },
  { header: 'Days accrued', get: r => r.accrual_days, type: 'number' },
  { header: 'Last advance', get: r => r.last_paid_date, type: 'date' },
  { header: 'Flag', get: r => (r.over_advanced ? 'OVER-ADVANCED' : '') },
]

const DAY_COLS: ExportColumn[] = [
  { header: 'Date', get: r => r.work_date, type: 'date' },
  { header: 'Rep', get: r => r.name, role: 'rep' },
  { header: 'Store', get: r => r.store_code, role: 'store' },
  { header: 'Market', get: r => r.market },
  { header: 'Daily base', get: r => r.base_amount, money: true },
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
    const parts = named.filter(([k]) => Number(c[k])).map(([k, lbl]) => `${lbl}: ${fmt(c[k])}`)
    if (parts.length) bits.push(parts.join(' · '))
  } else if (c.mode === 'tier_only') {
    bits.push('no sales this day')
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
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [drill, setDrill] = useState<any>(null)

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
    ]).then(([a, d, l, o]) => { setAcc(a); setDays(d); setLedger(l); setOver(o) })
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setBusy(false))
  }, [from, to])

  useEffect(() => { load() }, [load])

  const empRows = acc?.employees || []
  const empAcc = useMemo(() => ({
    rep: (r: any) => r.name,
    store: (r: any) => (r.store_codes || [])[0] || '',
    market: (r: any) => (r.markets || [])[0] || '',
  }), [])
  // Store/market are multi-valued per employee, so membership (not first-value) decides — a rep who
  // sold in two stores must survive a filter on either of them.
  const shownEmps = useMemo(() => empRows.filter((r: any) => {
    const f = filt
    if (f.reps.length && !f.reps.some(x => String(x).toLowerCase() === String(r.name || '').toLowerCase())) return false
    if (f.stores.length && !(r.store_codes || []).some((c: string) => f.stores.some(x => String(x).toLowerCase() === String(c).toLowerCase()))) return false
    if (f.markets.length && !(r.markets || []).some((c: string) => f.markets.some(x => String(x).toLowerCase() === String(c).toLowerCase()))) return false
    return true
  }), [empRows, filt])

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
    sublabel: `unpaid ${fmt(r.unpaid_balance)}${(r.store_codes || []).length ? ` · ${(r.store_codes).join(', ')}` : ''}`,
  })), [empRows])

  const t = acc?.totals || {}
  const shownTotals = useMemo(() => ({
    accrued: shownEmps.reduce((s: number, r: any) => s + Number(r.accrued_total || 0), 0),
    paid: shownEmps.reduce((s: number, r: any) => s + Number(r.paid_total || 0), 0),
    unpaid: shownEmps.reduce((s: number, r: any) => s + Number(r.unpaid_balance || 0), 0),
    today: shownEmps.reduce((s: number, r: any) => s + Number(r.today_accrual || 0), 0),
    flagged: shownEmps.filter((r: any) => r.over_advanced).length,
  }), [shownEmps])

  const notReady = acc && acc.ready === false

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
        : `Recorded ${fmt(Number(pAmt))} to ${chosen?.name || pEmp}. This is a cash advance — it changes paid/unpaid only.`)
      setPAmt(''); setPRef(''); setPNote('')
      load()
    }).catch(e => setMsg(String(e?.message || e))).finally(() => setSaving(false))
  }

  const runOne = () => {
    setRunning(true); setMsg('')
    api(`/api/v1/commcalc/payout/accrual/run?org_id=${encodeURIComponent(orgParam())}&date=${runDate}`, { method: 'POST' })
      .then((r: any) => {
        setMsg(r?.ready === false ? (r.note || 'Not set up yet.')
          : `${runDate}: ${r.employees || 0} rep(s), ${fmt(r.base_total || 0)} of daily base` +
            (r.tier_recognitions ? `, ${r.tier_recognitions} monthly true-up(s) recognized (${fmt(r.tier_recognized || 0)})` : '') +
            `. Re-running the same date restates it — it never adds twice.`)
        load()
      })
      .catch(e => setMsg(String(e?.message || e))).finally(() => setRunning(false))
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Daily Commission</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', lineHeight: 1.6 }}>
          What each rep has <b>probably</b> earned so far, what has been <b>advanced to them in cash</b>,
          and the balance. <b>Accrued is an expected figure, not a payslip</b> — it never changes anyone's
          pay, and a recorded advance never reduces what they are owed at month end.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt} periodMode="range"
          show={{ period: true, stores: true, markets: true, reps: true }}
          storeOptions={storeOpts} marketOptions={marketOpts} repOptions={repOpts}
          storeLabel="Stores…" marketLabel="Markets…" repLabel="Reps…"
          right={<button className="btn btn-secondary" onClick={load} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>}
        />
      </div>

      {err && <div className="card" style={{ borderLeft: '4px solid var(--red)', marginBottom: 14, fontSize: 13 }}>{err}</div>}
      {notReady && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', marginBottom: 14, fontSize: 13, lineHeight: 1.7 }}>
          <b>Daily commission accrual is not switched on yet.</b><br />{acc.note}
        </div>
      )}

      {acc?.ready && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <div className="card" style={tile}>
              <div style={tileCap}>Accrued (expected)</div>
              <div style={tileVal}>{fmt(shownTotals.accrued)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>never paid from this page</div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Paid in cash (advances)</div>
              <div style={tileVal}>{fmt(shownTotals.paid)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>from the envelope</div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Unpaid balance</div>
              <div style={{ ...tileVal, color: shownTotals.unpaid < 0 ? 'var(--red)' : 'var(--text)' }}>{fmt(shownTotals.unpaid)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>accrued − advanced</div>
            </div>
            <div className="card" style={tile}>
              <div style={tileCap}>Accrued on {to}</div>
              <div style={tileVal}>{fmt(shownTotals.today)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>that day's sales</div>
            </div>
            <div className="card" style={{ ...tile, borderColor: shownTotals.flagged ? 'var(--red)' : 'var(--border)' }}>
              <div style={tileCap}>Over-advanced</div>
              <div style={{ ...tileVal, color: shownTotals.flagged ? 'var(--red)' : 'var(--text)' }}>{shownTotals.flagged}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>flag only — nothing is clawed back</div>
            </div>
          </div>

          {/* ── over-advance review ─────────────────────────────────────────────────────── */}
          {(over?.running?.length || over?.monthly?.length) ? (
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
                  {[...(over.running || []), ...(over.monthly || [])].map((r: any, i: number) => (
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
              subtitle={`as of ${to} · accrued is EXPECTED commission, advances are cash out of the envelope`}
              filename={`daily-commission-by-rep-${to}`}
              columns={EMP_COLS} rows={shownEmps} totals compact stickyHeader
              rowStyle={(r: any) => (r.over_advanced ? { background: '#fef2f2' } : undefined)}
            />
          </div>

          {/* ── record an advance ───────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>Record a cash advance</div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10, lineHeight: 1.6 }}>
              Records money physically handed to a rep out of an envelope. It changes <b>paid</b> and
              <b> unpaid</b> only — it does not pay anybody through payroll, does not change the accrual,
              and is never netted against a later shortfall.
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
              <button className="btn btn-primary" onClick={record} disabled={saving || !pEmp || !pAmt}>
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

          {drill && (
            <div className="card" style={{ marginBottom: 14, borderLeft: '4px solid var(--blue, #2563eb)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontWeight: 700 }}>{drill.name} — {drill.work_date} — {fmt(drill.total_amount)}</div>
                <button className="btn btn-secondary" onClick={() => setDrill(null)}>Close</button>
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 8, lineHeight: 1.7 }}>
                {drill.components?.explain}
              </div>
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
                    {['Rule', 'Pays', 'Lines matched', 'Tiered', 'Amount'].map(h =>
                      <th key={h} style={{ textAlign: 'left', padding: '4px 6px', color: 'var(--text2)' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {drill.components.rules.map((rb: any, i: number) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '4px 6px' }}>{rb.label}</td>
                        <td style={{ padding: '4px 6px', color: 'var(--text3)' }}>{rb.payout_kind}</td>
                        <td style={{ padding: '4px 6px' }}>{rb.matched_lines}</td>
                        <td style={{ padding: '4px 6px', color: 'var(--text3)' }}>{rb.tiered ? 'yes (monthly)' : 'no'}</td>
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
              subtitle="every payout recorded against accrued commission (append-only; corrections are their own decision)"
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
              re-running a date restates it, never adds to it, and it is <b>not</b> a commission
              recalculation — no payout is touched.
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
