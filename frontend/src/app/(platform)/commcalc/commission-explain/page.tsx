'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, apiDownload, apiFetchBase64, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { SendReportButton } from '@/lib/send-report'
import { ReportShell } from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import PlanLineBreakdown from '../_lib/PlanLineBreakdown'
import { GoogleRatingChips, GoogleRatingDetail, useGoogleRatings } from '../_lib/googleRatings'

// "How was this commission calculated" — READ-ONLY per-rep drill-down. Shows the two engines that can
// pay a non-Boost (Total/Luxelink) rep: the Commission PLAN component (which plan attached, via which
// assignment, per-rule matched sale lines) and the MULTI-MONTH component (per-device M1..N installments
// with gate status/reason + the MA-file cross-reference). Plus a $0 explanation and an IMEI device search.

interface RepRow { epay_salesperson: string; storeops_name?: string; store?: string; market?: string; total_payout?: number }

const BASIS: Record<string, string> = {
  flat_per_unit: '$/unit', pct_gp: '% GP', pct_mrc: '% MRC',
  pct_price_over_cost: '% (price−cost)', flat: 'flat bonus',
}
const REASON_LABEL: Record<string, string> = {
  paid: 'Paid', no_mi_match: 'Held — dealer not paid (no raw_mi row)',
  line_inactive: 'Held — line inactive', residual_not_received: 'Held — residual not received',
  activation_payment_missing: 'Held — no first-month payment', withheld: 'Held',
  held_stored: 'Held — stored row (see rep explain)',
}
const reasonColor = (code: string) => code === 'paid' ? 'var(--green)' : 'var(--red)'

const PLAN_COLS: ExportColumn[] = [
  { header: 'Rule', get: r => r.rule }, { header: 'Date', get: r => r.date, type: 'date' },
  { header: 'Trans ID', get: r => r.trans_id }, { header: 'IMEI', get: r => r.imei },
  { header: 'MDN', get: r => r.mdn }, { header: 'Product', get: r => r.product },
  { header: 'Contract', get: r => r.contract_type }, { header: 'Basis', get: r => r.basis },
  { header: 'Ext Price', get: r => r.ext_price, money: true }, { header: 'GP', get: r => r.gp, money: true },
  // raw_sales has NO cost column — cost is IMPLIED (ext_price − gp). Showing it is what turns
  // "why did this pay $0" into "because the catalog says this item costs exactly what it sells for".
  { header: 'Implied cost', get: r => r.implied_cost, money: true },
  { header: 'Data check', get: r => r.cost_flag_labels || '' },
  // PAY GATE (mig 260/261): a line the gate stopped is still SHOWN, with the reason and the money it
  // would have paid. Silence is how a $0 becomes unexplainable — and how an overpayment hides.
  { header: 'Paid?', get: r => r.suppressed ? 'NOT PAID' : '' },
  { header: 'Why not', get: r => r.suppressed_reason || '' },
  { header: 'Would have paid', get: r => r.suppressed ? r.would_have_paid : '', money: true },
  { header: 'Line $', get: r => r.amount, money: true },
]
const INST_COLS: ExportColumn[] = [
  { header: 'IMEI', get: r => r.imei },
  // ONE line, always the same shape: DEVICE — RATE PLAN — MRC (owner 2026-07-27). The engine resolves
  // both halves of the activation, so this column never flips between the phone and the rate plan.
  { header: 'Device — Rate plan', get: r => r.product },
  { header: 'Category', get: r => r.device_category },
  { header: 'Month', get: r => r.month_index, type: 'number' },
  { header: 'Pay Period', get: r => r.pay_period, role: 'month' },
  { header: 'Status', get: r => r.status_label }, { header: 'Reason', get: r => r.hold_detail },
  { header: 'Paid $', get: r => r.amount, money: true },
  { header: 'Withheld $', get: r => r.withheld_amount, money: true },
  { header: 'MRC', get: r => r.mrc_at_pay, money: true },
  { header: 'MA says paid', get: r => r.ma_says_paid ? 'yes' : 'no' },
  // A month can be PAID and still be $0 (a %-of-MRC month with no identifiable rate-plan line). The
  // engine always knew why; the page never said. Now it does — and it exports (RULE FOUR).
  { header: 'Why $0', get: r => r.zero_note || '' },
]

export default function CommissionExplainPage() {
  const { period } = usePeriod()
  const [reps, setReps] = useState<RepRow[]>([])
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [rep, setRep] = useState('')
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [imei, setImei] = useState('')
  const [dev, setDev] = useState<any>(null)
  const [devBusy, setDevBusy] = useState(false)

  // roster (pick-don't-type) from the org-scoped rep_commissions rows
  useEffect(() => {
    api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setReps).catch(console.error)
    // deep-link ?rep= / ?imei= from the Rep Commission report drill-in
    const q = new URLSearchParams(window.location.search)
    if (q.get('rep')) setRep(q.get('rep') || '')
    if (q.get('imei')) { setImei(q.get('imei') || ''); }
  }, [period])

  const acc = { store: (r: RepRow) => r.store, market: (r: RepRow) => r.market, rep: (r: RepRow) => r.epay_salesperson }
  const opts = useMemo(() => optionsFromRows(reps, acc), [reps])   // eslint-disable-line react-hooks/exhaustive-deps
  const filteredReps = useMemo(() => filterRows(reps, filt, acc), [reps, filt])   // eslint-disable-line react-hooks/exhaustive-deps
  const repChoices = useMemo(() =>
    [...new Map(filteredReps.map(r => [r.storeops_name || r.epay_salesperson, r])).values()], [filteredReps])

  // Google store rating for the rep being explained (owner 2026-08-06). ONE batched summary call for the
  // reps in the picker, so switching reps never re-queries. Display-only — this page explains PAY, and the
  // rating changes nothing on it. Invisible until mod-people's google-reviews endpoints are live.
  const ratingNames = useMemo(() => repChoices.map(r => r.storeops_name || r.epay_salesperson), [repChoices])
  const { ratingsFor: googleFor } = useGoogleRatings(ratingNames)

  // if the bar selects exactly one rep, drive the explain from it
  useEffect(() => {
    if (filt.reps && filt.reps.length === 1) setRep(filt.reps[0])
  }, [filt.reps])

  useEffect(() => {
    if (!rep) { setData(null); return }
    setBusy(true); setData(null)
    api(`/api/v1/commcalc/commission-explain?org_id=${ORG_ID}&period=${encodeURIComponent(period)}&rep=${encodeURIComponent(rep)}`)
      .then(setData).catch(e => setData({ error: String(e?.message || e) })).finally(() => setBusy(false))
  }, [rep, period])

  function searchImei() {
    const v = imei.trim()
    if (!v) return
    setDevBusy(true); setDev(null)
    api(`/api/v1/commcalc/commission-device?org_id=${ORG_ID}&imei=${encodeURIComponent(v)}&period=${encodeURIComponent(period)}`)
      .then(setDev).catch(e => setDev({ error: String(e?.message || e) })).finally(() => setDevBusy(false))
  }

  // ── This rep's ITEMIZED COMMISSION STATEMENT (server-rendered PDF, reportlab) ──────────────────────
  // Same READ-ONLY document the /commcalc/reports page offers, delivered here so a rep's full calculation
  // can be handed to that employee. `rep` is the exact storeops_name || epay_salesperson string this page
  // already sends to /commission-explain, so it resolves identically in explain_rep. Download uses the
  // authed byte-download choke point; Send fetches the SAME PDF as base64 and posts it through the shared
  // /notify/send-file modal (the statement is rendered on the SERVER, so the in-browser export path can't
  // produce it — this is SendReportButton's serverFiles path).
  const statementUrl = () =>
    `/api/v1/commcalc/commission-statement?rep=${encodeURIComponent(rep)}&period=${encodeURIComponent(period)}&org_id=${ORG_ID}`
  function downloadStatement() {
    if (!rep) return
    apiDownload(statementUrl()).catch(e => alert(`Could not generate statement: ${e?.message || e}`))
  }
  async function statementFiles() {
    const b64 = await apiFetchBase64(statementUrl())
    const safe = `${rep}-${period}`.replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toLowerCase()
    return [{ filename: `commission-statement-${safe}.pdf`, mime: 'application/pdf', content_b64: b64 }]
  }

  const pc = data?.plan_component
  const mm = data?.multimonth_component
  const planRows = useMemo(() => {
    const out: any[] = []
    for (const r of (pc?.rules || [])) for (const l of (r.lines || []))
      out.push({ rule: r.label, basis: BASIS[r.payout_kind] || r.payout_kind, date: l.date, trans_id: l.trans_id,
        imei: l.imei, mdn: l.mdn, product: l.product, contract_type: l.contract_type,
        ext_price: l.ext_price, gp: l.gp, amount: l.flat_once ? null : l.amount,
        implied_cost: l.implied_cost, cost_flags: l.cost_flags || [],
        cost_flag_labels: (l.cost_flag_labels || []).join(' '),
        suppressed: !!l.suppressed, suppressed_by: l.suppressed_by || '',
        suppressed_reason: l.suppressed_reason || '', would_have_paid: l.would_have_paid ?? 0,
        basis_note: l.basis_note || '', amount_before_guard: l.amount_before_guard ?? null,
        // engine's own qualifying flag — drives the per-category UNIT count in the grouped
        // breakdown below. Display only; it changes no amount.
        qualifies: l.qualifies !== false })
    return out
  }, [pc])
  const instRows = useMemo(() => {
    const out: any[] = []
    for (const d of (mm?.devices || [])) for (const i of (d.installments || []))
      out.push({ imei: d.imei, product: i.label || d.label || d.product,
        device_category: i.device_category || d.device_category,
        month_index: i.month_index, pay_period: i.pay_period,
        status_label: REASON_LABEL[i.hold_reason] || i.status, hold_detail: i.hold_detail, amount: i.amount,
        withheld_amount: i.withheld_amount, mrc_at_pay: i.mrc_at_pay, ma_says_paid: d.ma_says_paid,
        zero_note: i.zero_note || '' })
    return out
  }, [mm])

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Incentive Explain — how was this calculated?</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · read-only · plan component + multi-month installments (M1–M6) with gate status &amp; the MA-file cross-reference
        </p>
      </div>

      <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false }}
        storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps} repLabel="Reps…" />

      {/* rep picker (pick-don't-type) + IMEI search */}
      <div className="card" style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', marginBottom: 16 }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Explain rep:&nbsp;
          <select className="select" value={rep} onChange={e => setRep(e.target.value)}>
            <option value="">Select a rep…</option>
            {repChoices.map(r => <option key={r.epay_salesperson} value={r.storeops_name || r.epay_salesperson}>
              {r.storeops_name || r.epay_salesperson}{r.store ? ` — ${r.store.substring(0, 20)}` : ''}</option>)}
          </select>
        </label>
        {/* This rep's itemized statement — download the server-rendered PDF, or send it to the employee.
            Shown once a rep is selected so it reads as tied to the explained rep. */}
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <button className="btn btn-secondary" onClick={downloadStatement} disabled={!rep}
            title="Download this rep's itemized incentive statement (line-by-line, PDF)">
            📄 Incentive Statement (PDF)
          </button>
          {rep && (
            <SendReportButton
              title={`Incentive statement — ${rep} — ${period}`}
              label="📤 Send statement"
              serverFiles={statementFiles} />
          )}
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input className="input" placeholder="Search IMEI / device serial…" value={imei}
            onChange={e => setImei(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') searchImei() }}
            style={{ width: 240, fontFamily: 'monospace' }} />
          <button className="btn btn-secondary" onClick={searchImei}>🔍 Device story</button>
        </div>
      </div>

      {/* This rep's Google store rating(s) — chips next to the person, full per-store detail below the
          pay explanation. Renders nothing when the tenant has no Google Reviews data. */}
      {rep && <div style={{ margin: '-8px 0 12px' }}><GoogleRatingChips list={googleFor(rep)} /></div>}

      {/* DEVICE STORY (IMEI search) */}
      {dev && (
        <div className="card" style={{ marginBottom: 16, borderLeft: '4px solid #7c3aed' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 700 }}>📟 Device story · IMEI {dev.imei}</div>
            <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDev(null)}>✕</button>
          </div>
          {devBusy ? <div style={{ padding: 20, color: 'var(--text3)' }}>Loading…</div>
            : dev.error ? <div style={{ color: 'var(--red)', padding: 12 }}>❌ {dev.error}</div>
            : dev.note ? <div style={{ color: 'var(--text3)', padding: 12 }}>{dev.note}</div>
            : <DeviceStory dev={dev} />}
        </div>
      )}

      {/* REP EXPLAIN */}
      {busy ? <div className="card" style={{ textAlign: 'center', padding: 40 }}><div className="spinner" style={{ margin: '0 auto' }} /></div>
        : data?.error ? <div className="card" style={{ color: 'var(--red)' }}>❌ {data.error}</div>
        : !data ? <div className="card" style={{ color: 'var(--text3)', textAlign: 'center', padding: 40 }}>
            Select a rep above (or open this page from a rep row on the Rep Incentive Report) to see how their incentive was calculated.
          </div>
        : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* $0 / explanation banner */}
            {(data.zero_explanation?.length > 0) && (
              <div className="card" style={{ borderLeft: '4px solid var(--amber)', background: 'var(--surface2)' }}>
                <div style={{ fontWeight: 700, marginBottom: 6 }}>Why is this number what it is?</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: 'var(--text2)', lineHeight: 1.7 }}>
                  {data.zero_explanation.map((z: string, i: number) => <li key={i}>{z}</li>)}
                </ul>
              </div>
            )}

            {/* PLAN COMPONENT */}
            <div className="card">
              <div style={{ fontWeight: 700, marginBottom: 8 }}>1 · Incentive-Plan component</div>
              {pc?.plan_name ? (
                <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>
                  Plan <b style={{ color: 'var(--text)' }}>{pc.plan_name}</b> attached via{' '}
                  {pc.assignment ? <span>the <b>{pc.assignment.scope}</b> assignment
                    {pc.assignment.scope_value ? <> = <b>“{pc.assignment.scope_value}”</b></> : null}
                    {' '}(specificity rank {pc.assignment.rank}, priority {pc.assignment.priority})</span> : 'an assignment'}.
                  {' '}Subtotal {fmt(pc.base_payout + pc.tiered_payout)} × tier {pc.tier_multiplier} ={' '}
                  <b style={{ color: 'var(--accent)' }}>{fmt(pc.total_payout)}</b>.
                </div>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--red)', marginBottom: 10 }}>
                  No incentive plan attached to this rep → $0 on the plan component.
                </div>
              )}
              <AssignmentTrace considered={pc?.considered} />
              <DataQualityBanner dq={pc?.data_quality} />
              <PayGateBanner rows={planRows} />
              {/* OWNER 2026-08-04: transaction-grouped, date→numeric-trans-id ordered, with a
                  per-category (plan-rule) breakdown + filter. Identical component to the 🔍 Plan
                  commission drill-down on /commcalc/reports so both surfaces read the same.
                  The flat ReportShell (full columns + Excel/PDF/email/WhatsApp, RULE FOUR) is kept
                  underneath and is fed the SAME rows in the SAME order with the SAME category
                  filter applied — what you see is what exports. */}
              {planRows.length > 0 ? (
                <PlanLineBreakdown rows={planRows}>
                  {(visible) => (
                    <ReportShell title={`Plan line detail — ${data.rep}`} subtitle={`${period} · ${pc?.plan_name || ''}`}
                      filename={`plan-detail-${data.rep}-${period}`.replace(/\s+/g, '-')} columns={PLAN_COLS} rows={visible} totals compact />
                  )}
                </PlanLineBreakdown>
              ) : pc?.plan_name ? (
                <div style={{ fontSize: 13, color: 'var(--text3)', marginTop: 8 }}>Plan attached but no rule matched a sale line (see explanation above).</div>
              ) : null}
            </div>

            {/* MULTI-MONTH COMPONENT */}
            <div className="card">
              <div style={{ fontWeight: 700, marginBottom: 8 }}>
                2 · Multi-month installments (M1–M6){mm?.schedules ? ` · ${mm.schedules} schedule(s)` : ''}
              </div>
              {mm?.note && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>{mm.note}</div>}
              {(mm?.devices?.length > 0) ? (
                <>
                  <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>
                    {mm.totals.paid} paid · {mm.totals.withheld} held · {fmt(mm.totals.amount)} paid this period.
                    {mm.devices.some((d: any) => d.held_but_ma_paid) && (
                      <span style={{ color: '#b45309', marginLeft: 8 }}>
                        ⚠ Some devices are HELD in-app while the MA file shows them paid — see the MA cross-reference per device.
                      </span>)}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {mm.devices.map((d: any, i: number) => <DeviceCard key={i} d={d} />)}
                  </div>
                  <EngineWarnings warnings={mm?.warnings} />
                  <div style={{ marginTop: 12 }}>
                    <ReportShell title={`Installment detail — ${data.rep}`} subtitle={period}
                      filename={`installments-${data.rep}-${period}`.replace(/\s+/g, '-')} columns={INST_COLS} rows={instRows} totals compact />
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--text3)' }}>No sale-triggered installments for this rep in {period}.</div>
              )}
            </div>

            {/* RECONCILIATION */}
            {data.reconciliation && (
              <div className="card" style={{ fontSize: 13 }}>
                <div style={{ fontWeight: 700, marginBottom: 6 }}>Reconciliation vs last Run Calculation</div>
                <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', color: 'var(--text2)' }}>
                  <span>Plan comm: <b>{fmt(data.reconciliation.plan_comm)}</b></span>
                  <span>Sale installments: <b>{fmt(data.reconciliation.installment_comm_sale)}</b></span>
                  <span>Residual (raw_mi): <b>{fmt(data.reconciliation.residual_installment_comm)}</b></span>
                  <span>Total payout: <b style={{ color: 'var(--accent)' }}>{fmt(data.reconciliation.total_payout)}</b></span>
                </div>
              </div>
            )}

            {/* GOOGLE STORE RATINGS for this rep — per store they work at, with Google's recent reviews
                collapsed behind a toggle. Context for the coaching conversation; it explains no dollar on
                this page and is excluded from every number above. */}
            <GoogleRatingDetail repName={rep} title={`Google store ratings — ${data.rep || rep}`} />
          </div>
        )}
    </div>
  )
}

// ── PAY-INPUT DATA QUALITY (owner report 2026-07-31) — DISPLAY ONLY ────────────────────────────────
// A %-of-GP payout is only as good as the GP it is paid on, and raw_sales has NO cost column (cost is
// implied: ext_price − gp). When the POS catalog carries cost == retail the GP is $0 and the payout is
// $0 by arithmetic; when cost is negative the payout inflates. Separately, a plan rule's rate is a
// FRACTION (0.10 = 10%) — a rate typed as a whole percent pays 100×. Nothing here changes a number;
// it explains the ones already on screen.
function DataQualityBanner({ dq }: { dq: any }) {
  if (!dq || (!dq.suspect_lines && !(dq.rate_issues || []).length)) return null
  return (
    <div style={{ border: '1px solid #f59e0b', background: '#fffbeb', borderRadius: 8, padding: 10, marginBottom: 10 }}>
      <div style={{ fontWeight: 700, fontSize: 13, color: '#92400e', marginBottom: 4 }}>
        ⚠ Check the inputs, not the maths — this is a source-data finding. Nothing below was changed.
      </div>
      {(dq.rate_issues || []).map((r: any, i: number) => (
        <div key={`r${i}`} style={{ fontSize: 12.5, color: '#92400e', marginBottom: 4 }}>
          <b>Rule “{r.label || r.rule_id}”</b> pays <b>{r.payout_kind}</b> at a stored rate of <b>{r.pct}</b>.
          {' '}{(r.labels || []).join(' ')}
        </div>
      ))}
      {dq.suspect_lines > 0 && (
        <div style={{ fontSize: 12.5, color: '#92400e' }}>
          <b>{dq.suspect_lines}</b> of <b>{dq.checked_lines}</b> matched lines have an unusable cost:
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {(dq.by_flag || []).map((f: any, i: number) => (
              <li key={i}>
                <b>{f.lines}</b> line{f.lines === 1 ? '' : 's'} ({fmt(f.ext_price)} sold, {fmt(f.paid)} paid) — {f.label}
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 6 }}>
            <a href="/commcalc/accessory-cost-audit" style={{ color: 'var(--accent)' }}>
              Open the Accessory Cost Audit → the item list + what each option would have paid
            </a>
          </div>
        </div>
      )}
      <div style={{ fontSize: 11, color: '#92400e', marginTop: 6, opacity: 0.85 }}>{dq.note}</div>
    </div>
  )
}

// PAY GATE (mig 260/261) — the lines a rule matched but the gate did not pay, and why. This is the
// surface the owner reads after "why did this one sale pay $200": the seven suppressed lines are named
// here with the dollars they would have paid, instead of quietly disappearing from the drill-down.
function PayGateBanner({ rows }: { rows: any[] }) {
  const supp = (rows || []).filter(r => r.suppressed)
  const guarded = (rows || []).filter(r => r.amount_before_guard != null)
  if (!supp.length && !guarded.length) return null
  const byReason: Record<string, { n: number; amt: number }> = {}
  for (const r of supp) {
    const k = r.suppressed_reason || r.suppressed_by || 'suppressed'
    byReason[k] = byReason[k] || { n: 0, amt: 0 }
    byReason[k].n += 1; byReason[k].amt += Number(r.would_have_paid || 0)
  }
  return (
    <div style={{ border: '1px solid #93c5fd', background: '#eff6ff', borderRadius: 8, padding: 10, margin: '8px 0' }}>
      <div style={{ fontWeight: 700, fontSize: 13, color: '#1e3a8a', marginBottom: 4 }}>
        🧾 Pay gate — {supp.length} matched line{supp.length === 1 ? '' : 's'} did not pay
        {guarded.length > 0 ? `, ${guarded.length} paid on a guarded basis` : ''}
      </div>
      <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12.5, color: '#1e3a8a' }}>
        {Object.entries(byReason).map(([k, v]) => (
          <li key={k}><b>{v.n}</b> line{v.n === 1 ? '' : 's'} ({fmt(v.amt)} not paid) — {k}</li>
        ))}
        {guarded.length > 0 && <li><b>{guarded.length}</b> line(s) had an unusable GP, so the rate was paid on the price instead — see “Why not”.</li>}
      </ul>
      <div style={{ fontSize: 11, color: '#1e3a8a', marginTop: 6, opacity: 0.85 }}>
        Nothing is hidden: every one of these lines is still listed below with the money it would have paid.
      </div>
    </div>
  )
}

// The multi-month engine already records WHY a chain resolved to $0 (no rate-plan line found, an
// ambiguous MRC, an unclassifiable category). Those warnings never reached this page.
function EngineWarnings({ warnings }: { warnings: any[] }) {
  const [open, setOpen] = useState(false)
  const w = warnings || []
  if (!w.length) return null
  return (
    <div style={{ marginTop: 10 }}>
      <button className="btn btn-secondary" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => setOpen(o => !o)}>
        {open ? '▾' : '▸'} Engine notes for this rep ({w.length})
      </button>
      {open && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          {w.map((x: any, i: number) => (
            <li key={i}>
              <b>{x.type}</b>{x.month_index ? ` · M${x.month_index}` : ''}
              {x.trans_id ? ` · trans ${x.trans_id}` : ''}{x.imei ? ` · ${x.imei}` : ''} — {x.detail}
              {(x.products || []).length > 0 && (
                <div style={{ color: 'var(--text3)' }}>lines seen: {(x.products || []).join(' · ')}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AssignmentTrace({ considered }: { considered: any[] }) {
  const [open, setOpen] = useState(false)
  if (!considered || considered.length === 0) return null
  const misses = considered.filter(c => !c.matched)
  return (
    <div style={{ marginBottom: 10 }}>
      <button className="btn btn-secondary" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => setOpen(o => !o)}>
        {open ? '▾' : '▸'} Assignment trace ({considered.filter(c => c.matched).length} matched, {misses.length} nearest-miss)
      </button>
      {open && (
        <table style={{ width: '100%', fontSize: 12, marginTop: 6 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['', 'Plan', 'Scope', 'Value', 'Rank', 'Priority', 'Why'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '4px 6px', color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {considered.map((c, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 6px' }}>{c.matched ? '✅' : '—'}</td>
                <td style={{ padding: '4px 6px' }}>{c.plan_name}</td>
                <td style={{ padding: '4px 6px' }}>{c.scope}</td>
                <td style={{ padding: '4px 6px' }}>{c.scope_value || '—'}</td>
                <td style={{ padding: '4px 6px' }}>{c.rank ?? '—'}</td>
                <td style={{ padding: '4px 6px' }}>{c.priority ?? '—'}</td>
                <td style={{ padding: '4px 6px', color: c.matched ? 'var(--green)' : 'var(--text3)' }}>{c.matched ? 'winner-eligible' : c.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function DeviceCard({ d }: { d: any }) {
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 13 }}>
          <b>IMEI {d.imei || '—'}</b>{d.mdn ? ` · MDN ${d.mdn}` : ''}
          {(d.label || d.product) ? ` · ${d.label || d.product}` : ''}
          {d.device_category ? ` · ${d.device_category}` : ''}
          {d.contract_type ? ` · ${d.contract_type}` : ''}{d.sale_period ? ` · sold ${d.sale_period}` : ''}
        </div>
        {d.held_but_ma_paid && <span className="badge" style={{ background: '#fef3c7', color: '#92400e' }}>MA paid · in-app held</span>}
      </div>
      <table style={{ width: '100%', fontSize: 12, marginTop: 6 }}>
        <thead><tr style={{ background: 'var(--surface2)' }}>
          {['Month', 'Device — Rate plan', 'Pay period', 'Status', 'Paid', 'Held', 'MRC', 'raw_mi match', 'Reason'].map(h =>
            <th key={h} style={{ textAlign: 'left', padding: '3px 6px', color: 'var(--text2)', fontSize: 10 }}>{h}</th>)}
        </tr></thead>
        <tbody>
          {d.installments.map((i: any, k: number) => (
            <tr key={k} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '3px 6px' }}>M{i.month_index}</td>
              <td style={{ padding: '3px 6px', fontSize: 11 }} title={i.label || ''}>{i.label || d.label || '—'}</td>
              <td style={{ padding: '3px 6px' }}>{i.pay_period}</td>
              <td style={{ padding: '3px 6px', color: reasonColor(i.hold_reason), fontWeight: 600 }}>{REASON_LABEL[i.hold_reason] || i.status}</td>
              <td style={{ padding: '3px 6px' }}>{i.status === 'paid' ? fmt(i.amount) : '—'}</td>
              <td style={{ padding: '3px 6px', color: 'var(--red)' }}>{i.status !== 'paid' && i.withheld_amount != null ? fmt(i.withheld_amount) : '—'}</td>
              <td style={{ padding: '3px 6px' }}>{i.mrc_at_pay != null ? fmt(i.mrc_at_pay) : '—'}</td>
              <td style={{ padding: '3px 6px' }}>{i.mi_ref ? `${i.mi_ref.subscriber_status || 'found'}` : 'none'}</td>
              <td style={{ padding: '3px 6px', color: 'var(--text3)' }}>
                {i.hold_detail}
                {i.zero_note ? <div style={{ color: '#b45309', marginTop: 2 }}>⚠ {i.zero_note}</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.ma_matches?.length > 0 && <MaTable rows={d.ma_matches} />}
    </div>
  )
}

function MaTable({ rows }: { rows: any[] }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#6d28d9', marginBottom: 3 }}>
        MA-file reference ({rows.length} row{rows.length === 1 ? '' : 's'}) — amounts sign-normalized to “paid to dealer”
      </div>
      <table style={{ width: '100%', fontSize: 11 }}>
        <thead><tr style={{ background: 'var(--surface2)' }}>
          {['Period', 'Order', 'BAN', 'Status', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'Rebate'].map(h =>
            <th key={h} style={{ textAlign: 'left', padding: '3px 5px', color: 'var(--text2)' }}>{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((m, i) => (
            <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '3px 5px' }}>{m.period}</td>
              <td style={{ padding: '3px 5px', fontFamily: 'monospace' }}>{m.activation_order || '—'}</td>
              <td style={{ padding: '3px 5px' }}>{m.ban || '—'}</td>
              <td style={{ padding: '3px 5px' }}>{m.line_status || '—'}</td>
              {[1, 2, 3, 4, 5, 6].map(n => <td key={n} style={{ padding: '3px 5px' }}>{m.spiffs_paid?.[`m${n}`] ? fmt(m.spiffs_paid[`m${n}`]) : '—'}</td>)}
              <td style={{ padding: '3px 5px' }}>{m.rebate_paid ? fmt(m.rebate_paid) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DeviceStory({ dev }: { dev: any }) {
  const instRows = (dev.installments || []).map((i: any) => ({
    imei: dev.imei, product: i.label || '', device_category: i.device_category,
    month_index: i.month_index, pay_period: i.pay_period,
    status_label: REASON_LABEL[i.hold_reason] || i.status, hold_detail: i.hold_detail, amount: i.amount,
    withheld_amount: i.withheld_amount, mrc_at_pay: i.mrc_at_pay, ma_says_paid: dev.ma_says_paid,
  }))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
      {dev.held_but_ma_paid || (dev.ma_says_paid && (dev.installments || []).some((i: any) => i.status !== 'paid'))
        ? <div style={{ fontSize: 12, color: '#b45309' }}>⚠ In-app installments are HELD while the MA file shows this device paid.</div> : null}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Sale line(s)</div>
        <table style={{ width: '100%', fontSize: 12 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Date', 'Period', 'Store', 'Rep', 'Product', 'Contract', 'Ext Price', 'GP'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '3px 6px', color: 'var(--text2)' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {(dev.sale_lines || []).map((s: any, i: number) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '3px 6px' }}>{s.date}</td><td style={{ padding: '3px 6px' }}>{s.period}</td>
                <td style={{ padding: '3px 6px' }}>{s.store}</td><td style={{ padding: '3px 6px' }}>{s.salesperson}</td>
                <td style={{ padding: '3px 6px' }}>{s.product}</td><td style={{ padding: '3px 6px' }}>{s.contract_type}</td>
                <td style={{ padding: '3px 6px' }}>{fmt(s.ext_price)}</td><td style={{ padding: '3px 6px' }}>{fmt(s.gp)}</td>
              </tr>
            ))}
            {(dev.sale_lines || []).length === 0 && <tr><td colSpan={8} style={{ padding: 8, color: 'var(--text3)' }}>No sale line found.</td></tr>}
          </tbody>
        </table>
      </div>
      {(dev.plan_pay || []).length > 0 && (
        <div style={{ fontSize: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Plan pay for this device</div>
          {dev.plan_pay.map((p: any, i: number) => (
            <div key={i} style={{ color: 'var(--text2)' }}>{p.period} · {p.rep} · {p.plan_name} · {p.rule}: <b>{fmt(p.amount)}</b></div>
          ))}
        </div>
      )}
      {instRows.length > 0 && (
        <ReportShell title={`Device installments — ${dev.imei}`} filename={`device-${dev.imei}`}
          columns={INST_COLS} rows={instRows} totals compact />
      )}
      {(dev.ma_matches || []).length > 0 && <MaTable rows={dev.ma_matches} />}
    </div>
  )
}
