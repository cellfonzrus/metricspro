'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { StoreOpt } from '@/lib/market-store-cascade'
import DrillDownPanel, { DrillTileLinks, type DrillPanel } from '@/components/DrillDownPanel'
import { CASH_CAL_BASIS_WORD, cashCalWeeks } from '@/lib/cash-calendar-view'
import { sourceLabel } from '@/lib/sourceLabels'
import { PlatformDetail, SetupNotice } from '@/lib/setupNotice'

// CASH CALENDAR — expected money IN, money OWED out, and the net position, day by day (§COMMCALC-56).
//
// OWNER ASK, verbatim: *"i need a report for the expected payments for teh next payout cycle by the
// day as acalender display for all tenants, also the foloowing report will be how much is owed,
// which we already ahave and the thent eh irs will be after offseting the payments what isteh cash
// flow"*.
//
// THE ONE THING THIS SCREEN MUST NOT DO IS PRINT A ZERO WHEN IT MEANS "NOBODY DECLARED WHEN WE GET
// PAID". A tenant with no declared payout cycle has an UNKNOWN inbound, and $0.00 is not unknown:
// drawn as zero it would show the owner a cash position that is wrong by exactly the amount nobody
// measured, and he would plan against it. So a day cell has THREE states and never two —
//
//   measured, with money   ·   measured, and genuinely quiet   ·   NOT MEASURED
//
// — and the third says so in words, on the screen and in every export. Same posture as
// /commcalc/zero-sales; this page adds no second notion of "no data".
//
// DUPLICATE CHECK (CLAUDE.md build gate). The OWED half already exists and is not rebuilt here: the
// endpoint reads `payables/owed_read.dated_owed`, the SAME read /payables (Daily Owed) and
// /asset/owed-weekly share, so this calendar cannot total something those screens disagree with. The
// IN half had no home anywhere and got one (`payables/expected_receipts` over each tenant's OWN
// declared source and payout cycle). The netting is `payables/cash_calendar`, pure.
//
// RULE TWO: no carrier, tenant, market or store name appears here. Store codes, market names, the
// payout cycles and the basis words are DATA, rendered from the payload.

type Day = {
  date: string
  expected_in: number | null
  owed_out: number | null
  net: number | null
  running: number | null
  in_count: number | null
  out_count: number | null
  quiet: boolean
}

type Report = {
  days: Day[]
  total_in: number | null
  total_out: number | null
  total_net: number | null
  opening_balance: number | null
  closing_balance: number | null
  in_measured: boolean
  out_measured: boolean
  out_of_span_in: number | null
  out_of_span_out: number | null
  undated_in: number
  undated_out: number
  span_truncated: boolean
  start: string | null
  end: string | null
  note: string | null
  window: { start?: string; end?: string } | null
  expected_source: string | null
  expected_sources: Array<{ label: string; source_table: string; cadence: string | null; cadence_declared: boolean; total: number | null; measured: boolean; note: string | null }>
  expected_declared: boolean
  expected_table_missing: boolean
  expected_error: string | null
  expected_note: string | null
  expected_bases: Record<string, number>
  expected_unplaced_amount: number
  expected_unplaced_rows: number
  owed_source: string | null
  owed_grain: string | null
  owed_note: string | null
  drilldowns: Record<string, DrillPanel>
}

const money = (n: number | null | undefined) =>
  n == null ? 'not measured' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)
const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** An error in words, without an `any`. One home so both fetch paths word a failure identically. */
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** One declared inbound money source, as `GET /payables/receipt-sources` returns it. */
type ReceiptSourceRow = {
  label: string
  source_table: string
  amount_field: string
  payout_cadence: string | null
  payout_weekday: number | null
  payout_days: number[] | null
  payout_day_of_month: number | null
  payout_net_days: number | null
  is_active: boolean
}

export default function CashCalendarPage() {
  const [sel, setSel] = useState<StandardFilterValue>(emptyStandardFilter())
  const [report, setReport] = useState<Report | null>(null)
  const [opts, setOpts] = useState<{ stores?: StoreOpt[]; markets?: string[] }>({})
  const [opening, setOpening] = useState('')
  const [showSources, setShowSources] = useState(false)
  const [openDrill, setOpenDrill] = useState<string | null>(null)
  const [err, setErr] = useState('')

  // SERVER-SIDE NARROWING, deliberately. The store, market and date filters are the window the
  // endpoint reads the money through — narrowing client-side would leave the totals computed over
  // rows the user is no longer looking at, which is the §CORE-17 defect three shipped reports had.
  // THE QUERY STRING IS THE WHOLE REQUEST, derived rather than held in state, so the effect below
  // has exactly one dependency and cannot re-fire on an unrelated render.
  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (sel.period) p.set('start', sel.period)
    if (sel.periodTo) p.set('end', sel.periodTo)
    if (sel.stores.length) p.set('store', sel.stores.join(','))
    if (sel.markets.length) p.set('market', sel.markets.join(','))
    if (opening.trim()) p.set('opening_balance', opening.trim())
    return p.toString()
  }, [sel.period, sel.periodTo, sel.stores, sel.markets, opening])

  // `reloadKey` is how a save asks for a fresh read: bumping it from a click handler re-runs the
  // effect, where calling the loader directly would be a setState in an effect body.
  const [reloadKey, setReloadKey] = useState(0)
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data: Report = await api(`/api/v1/payables/cash-calendar?${query}`)
        if (alive) { setReport(data); setErr('') }
      } catch (e: unknown) {
        if (alive) setErr(errText(e))
      }
    })()
    return () => { alive = false }
  }, [query, reloadKey])
  useEffect(() => {
    let alive = true
    api('/api/v1/payables/filter-options')
      .then(o => { if (alive) setOpts(o) })
      .catch(() => { if (alive) setOpts({}) })
    return () => { alive = false }
  }, [])

  const weeks = useMemo(() => cashCalWeeks(report?.days || []), [report])
  const rows = useMemo(() => report?.days || [], [report])

  // The basis the money was dated by, in the WORDS the backend's one map supplies — never a sentence
  // per cadence typed here, which would drift from the home the moment a cadence is added.
  const basisLine = useMemo(() => {
    const b = Object.keys(report?.expected_bases || {})
    return b.length ? b.map(k => CASH_CAL_BASIS_WORD[k] || k).join(' · ') : ''
  }, [report])

  const columns: ExportColumn[] = useMemo(() => ([
    { header: 'Date', field: 'date', type: 'date' as const, role: 'date' as const,
      get: (r: Day) => r.date, tip: 'The day money is expected to arrive or falls due.' },
    { header: 'Expected in', field: 'expected_in', money: true,
      get: (r: Day) => (r.expected_in == null ? 'not measured' : r.expected_in),
      tip: 'Money a carrier or processor is expected to pay us on this day, placed by that carrier’s declared payout cycle. Blank means nobody has declared when it pays.' },
    { header: 'Owed out', field: 'owed_out', money: true,
      get: (r: Day) => (r.owed_out == null ? 'not measured' : r.owed_out),
      tip: 'What falls due to the distributor on this day — the same figure the Daily Owed report shows, read from the one shared source.' },
    { header: 'Net', field: 'net', money: true,
      get: (r: Day) => (r.net == null ? 'not measured' : r.net),
      tip: 'This day’s expected in minus this day’s owed out. Blank whenever either side was not measured: a net against an unknown is not a net.' },
    { header: 'Running position', field: 'running', money: true,
      get: (r: Day) => (r.running == null ? 'not measured' : r.running),
      tip: 'The opening balance carried forward through each day’s net — what is left after offsetting.' },
  ]), [])

  const notice = useMemo(() => {
    const parts: string[] = []
    if (report?.expected_table_missing) parts.push('Setup is not finished: the table that holds payout-cycle declarations does not exist yet, so no expected payment can be placed on a day.')
    else if (!report?.expected_declared) parts.push('No inbound money source is declared for this tenant, so expected payments are NOT MEASURED — not zero. Declare which feed states what the carrier will pay us and on what cycle it pays.')
    else if (report?.expected_error) parts.push(`A payout declaration exists but could not be read, so the expected column is incomplete: ${report.expected_error}`)
    if (report && !report.out_measured) parts.push('What is owed could not be measured, so no net position is shown.')
    if (report?.span_truncated) parts.push('The date span asked for was longer than one calendar can carry and was cut short.')
    if (report?.expected_unplaced_amount) parts.push(`${money(report.expected_unplaced_amount)} of expected money could not be placed on any day (${report.expected_unplaced_rows} rows) and is reported beside the calendar rather than folded into it.`)
    if (report?.owed_note) parts.push(report.owed_note)
    return parts.length ? parts.join(' ') : undefined
  }, [report])

  const tone: 'info' | 'warn' | 'good' =
    (report && (!report.expected_declared || report.expected_error || !report.out_measured)) ? 'warn' : 'info'

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <StandardFilterBar
        value={sel} onChange={setSel}
        periodMode="range"
        show={{ period: true, stores: true, markets: true, reps: false }}
        cascadeStores={opts.stores}
        storeOptions={opts.stores}
        marketOptions={opts.markets}
        right={
          <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            Opening balance
            <input value={opening} onChange={e => setOpening(e.target.value)} placeholder="0.00"
              inputMode="decimal" style={{ width: 96, padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }} />
          </label>
        }
      />

      <ReportShell
        title="Cash Calendar"
        subtitle={
          report
            ? `${report.start || '—'} → ${report.end || '—'} · in ${money(report.total_in)} · owed ${money(report.total_out)} · net ${money(report.total_net)} · closing ${money(report.closing_balance)}${basisLine ? ` · dated by ${basisLine}` : ''}`
            : (err ? '' : 'Loading…')
        }
        notice={err || notice}
        noticeTone={err ? 'warn' : tone}
        filename={`cash-calendar_${report?.start || ''}_${report?.end || ''}`}
        columns={columns}
        rows={rows}
        totals
      >
        {/* EVERY FIGURE NAMES ITS ROWS (§CORE-17) — the three headline totals open to the day rows
            they were folded from, through the ONE shared panel. The closing balance deliberately
            has none: the opening balance is not a row, so no set of rows adds up to it
            (§CORE-17.3h), and a panel for it would show a tie-out that cannot fail. */}
        <DrillTileLinks
          drills={Object.entries(report?.drilldowns || {}).map(([key, p]) => ({ key, label: p.figure?.label || key }))}
          open={openDrill} onToggle={k => setOpenDrill(v => (v === k ? null : k))} />
        {openDrill && report?.drilldowns?.[openDrill] && (
          <DrillDownPanel panel={report.drilldowns[openDrill]} />
        )}

        {/* ── THE CALENDAR ITSELF — a month grid of day cells, which is what was asked for ────── */}
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 4 }}>
            {WEEKDAY_LABELS.map(w => (
              <div key={w} style={{ fontSize: 11, color: 'var(--text3)', textAlign: 'center', fontWeight: 600 }}>{w}</div>
            ))}
          </div>
          {weeks.map((week, wi) => (
            <div key={wi} style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 4 }}>
              {week.map((d, di) => (
                <div key={d ? d.date : `pad-${di}`}
                  style={{
                    minHeight: 86, padding: 6, borderRadius: 8, fontSize: 11,
                    border: '1px solid var(--border)',
                    background: !d ? 'transparent'
                      : d.net == null ? 'var(--surface2)'
                        : d.quiet ? 'var(--surface)'
                          : d.net < 0 ? 'color-mix(in srgb, var(--amber, #f59e0b) 10%, var(--surface))'
                            : 'color-mix(in srgb, var(--green) 10%, var(--surface))',
                    opacity: d ? 1 : 0,
                  }}>
                  {d && (
                    <>
                      <div style={{ color: 'var(--text3)', fontWeight: 600 }}>{d.date.slice(8)}</div>
                      {/* NOT MEASURED IS SAID IN WORDS. A zero here would promise the owner cash he
                          has no evidence for, which is the whole reason this report exists. */}
                      {d.expected_in == null
                        ? <div style={{ color: 'var(--text3)', fontStyle: 'italic' }}>in: not measured</div>
                        : d.expected_in > 0 ? <div style={{ color: 'var(--green)' }}>+{money(d.expected_in)}</div> : null}
                      {d.owed_out == null
                        ? <div style={{ color: 'var(--text3)', fontStyle: 'italic' }}>owed: not measured</div>
                        : d.owed_out > 0 ? <div style={{ color: 'var(--amber, #f59e0b)' }}>-{money(d.owed_out)}</div> : null}
                      {d.net != null && !d.quiet && (
                        <div style={{ marginTop: 3, borderTop: '1px solid var(--border)', paddingTop: 3, fontWeight: 600 }}>
                          {money(d.net)}
                        </div>
                      )}
                      {d.quiet && <div style={{ color: 'var(--text3)' }}>nothing due</div>}
                      {d.running != null && (
                        <div style={{ color: 'var(--text3)' }}>bal {money(d.running)}</div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* WHAT ANSWERED, AND WHAT TO DO WHEN NOTHING DID. An empty calendar must carry its own next
            step rather than sending the reader to a support ticket. */}
        <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text2)', display: 'grid', gap: 4 }}>
          <div>
            Expected in:{' '}
            {report?.expected_declared
              ? (report.expected_sources || []).map(s =>
                  `${s.label} (${sourceLabel(s.source_table)}, ${s.cadence?.replace(/_/g, ' ') || 'no payout cycle declared'}) ${money(s.total)}`).join(' · ') || 'nothing placed'
              : 'nothing declared'}
          </div>
          <div>
            Owed out: {report?.owed_source ? sourceLabel(report.owed_source) : 'not measured'}
            {report?.owed_grain ? ` (per ${report.owed_grain.replace('_', ' ')})` : ''}
          </div>
          {/* OUT-OF-SPAN IS A GUARD, NOT A FEATURE, and it is structurally zero here: both sides
              are read THROUGH the same window, so nothing dated outside it ever reaches the fold.
              It renders only if it is ever non-zero, which would mean a caller handed the netting
              rows wider than the span it was asked for — a bug, and better seen than hidden. */}
          {(report?.out_of_span_in || report?.out_of_span_out) ? (
            <div>
              Dated outside these dates but counted anyway: {money(report?.out_of_span_in ?? null)} in,{' '}
              {money(report?.out_of_span_out ?? null)} out. Both sides are read through this window, so
              this should always be nothing — please report it.
            </div>
          ) : null}
          {/* THE NEXT STEP LIVES ON THE REPORT. An empty calendar whose cause is a missing
              declaration must carry the fix, not send the reader to a support ticket. */}
          <div>
            <button type="button" onClick={() => setShowSources(v => !v)}
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--accent, #2563eb)', font: 'inherit' }}>
              {showSources ? 'Hide' : 'Declare'} this tenant&rsquo;s payout cycles
            </button>{' '}
            — one row per inbound money source. Nothing is assumed: an undeclared carrier reads as
            not measured so that no day shows cash nobody promised.
          </div>
          {showSources && <ReceiptSourcePanel onSaved={() => setReloadKey(k => k + 1)} />}
        </div>
      </ReportShell>
    </div>
  )
}


// ── THE DECLARATION PANEL — one row per inbound money source (§COMMCALC-56) ──────────────────────
// The CADENCES and the CANDIDATE FEEDS are both fetched from the backend, never listed here: the
// cadence vocabulary is `payables/receipt_schedule.CADENCES`' to rule on, and only a relation the
// lineage registry declares a day-grain column for can be placed on a calendar at all. A list typed
// into this form would be a second copy of both, free to offer a choice the reader cannot honour.
function ReceiptSourcePanel({ onSaved }: { onSaved: () => void }) {
  const [state, setState] = useState<{
    rows: ReceiptSourceRow[]; cadences: string[]; cadence_words: Record<string, string>
    candidate_tables: string[]; table_missing: boolean; error: string | null
  } | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const refresh = useCallback(() => {
    api('/api/v1/payables/receipt-sources').then(setState).catch(e => setMsg(String(e?.message || e)))
  }, [])
  useEffect(refresh, [refresh])

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/payables/receipt-sources', {
        method: 'PUT',
        body: JSON.stringify({
          label: form.label || undefined,
          source_table: form.source_table,
          amount_field: form.amount_field,
          payout_cadence: form.payout_cadence || null,
          payout_weekday: form.payout_weekday === '' || form.payout_weekday == null ? null : Number(form.payout_weekday),
          payout_days: form.payout_days || null,
          payout_day_of_month: form.payout_day_of_month ? Number(form.payout_day_of_month) : null,
          payout_net_days: form.payout_net_days === '' || form.payout_net_days == null ? null : Number(form.payout_net_days),
        }),
      })
      setMsg('Saved.'); refresh(); onSaved()
    } catch (e: unknown) { setMsg(errText(e)) } finally { setBusy(false) }
  }

  const fld: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }
  return (
    <div style={{ marginTop: 8, padding: 10, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface2)', display: 'grid', gap: 8 }}>
      {/* THE MIGRATION NAME IS AN INTERNAL DETAIL (§19.36): `SetupNotice` shows the customer one
          sentence and reveals the file to the platform super admin alone. */}
      {state?.table_missing && (
        <div style={{ fontSize: 12 }}>
          <SetupNotice lead="Payout cycles cannot be declared yet." detail="1115_receipt_source_map.sql" />
        </div>
      )}
      {!!state?.rows?.length && (
        <div style={{ fontSize: 12, display: 'grid', gap: 2 }}>
          {state.rows.map((r, i) => (
            <div key={i}>
              {r.label} — {sourceLabel(r.source_table)} — {r.payout_cadence?.replace(/_/g, ' ') || 'no payout cycle declared'}
              {r.payout_weekday != null ? ` (weekday ${r.payout_weekday})` : ''}
              {r.payout_days ? ` (days ${String(r.payout_days)})` : ''}
              {r.payout_day_of_month != null ? ` (day ${r.payout_day_of_month})` : ''}
              {r.payout_net_days != null ? ` (+${r.payout_net_days} days)` : ''}
              {r.is_active === false ? ' — off' : ''}
              {/* The relation and the money column are internals, so they ride PlatformDetail (the
                  platform super admin only) as BOUND values rather than composed into copy. */}
              <PlatformDetail detail={r.source_table} style={{ marginLeft: 6 }} />
              <PlatformDetail detail={r.amount_field} style={{ marginLeft: 4 }} />
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <input placeholder="label" style={fld} value={form.label || ''} onChange={e => setForm({ ...form, label: e.target.value })} />
        <select style={fld} value={form.source_table || ''} onChange={e => setForm({ ...form, source_table: e.target.value })}>
          <option value="">which feed states what they will pay us…</option>
          {(state?.candidate_tables || []).map(t => <option key={t} value={t}>{sourceLabel(t)}</option>)}
        </select>
        <input placeholder="amount column" style={fld} value={form.amount_field || ''} onChange={e => setForm({ ...form, amount_field: e.target.value })} />
        <select style={fld} value={form.payout_cadence || ''} onChange={e => setForm({ ...form, payout_cadence: e.target.value })}>
          <option value="">payout cycle…</option>
          {(state?.cadences || []).map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
        </select>
        {form.payout_cadence === 'weekly' && (
          <select style={fld} value={form.payout_weekday ?? ''} onChange={e => setForm({ ...form, payout_weekday: e.target.value })}>
            <option value="">pay weekday…</option>
            {WEEKDAY_LABELS.map((w, i) => <option key={w} value={String(i)}>{w}</option>)}
          </select>
        )}
        {form.payout_cadence === 'semi_monthly' && (
          <input placeholder="days of month, e.g. 6,21" style={fld} value={form.payout_days || ''} onChange={e => setForm({ ...form, payout_days: e.target.value })} />
        )}
        {form.payout_cadence === 'monthly' && (
          <input placeholder="day of month" inputMode="numeric" style={fld} value={form.payout_day_of_month || ''} onChange={e => setForm({ ...form, payout_day_of_month: e.target.value })} />
        )}
        {form.payout_cadence === 'net_days_after_period_end' && (
          <input placeholder="days after earning" inputMode="numeric" style={fld} value={form.payout_net_days || ''} onChange={e => setForm({ ...form, payout_net_days: e.target.value })} />
        )}
        <button type="button" disabled={busy || !form.source_table || !form.amount_field} onClick={save}
          style={{ padding: '5px 11px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: 'pointer' }}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
      {(msg || state?.error) && <div style={{ fontSize: 12, color: 'var(--text2)' }}>{msg || state?.error}</div>}
    </div>
  )
}
