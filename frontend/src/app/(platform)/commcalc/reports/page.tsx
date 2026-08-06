'use client'
import { useState, useEffect, useMemo, useRef } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { SendReportButton } from '@/lib/send-report'
import { ExportButtons, type ExportPayload } from '@/lib/export'
import { buildCommissionExport, payloadToCsv, repLabel, type CommissionExportInput, type CommissionTab } from '../_lib/commissionExport'
import { useAuth } from '@/lib/auth-context'
import { carrierMode } from '@/lib/rbac'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, isStandardFilterActive, type StandardFilterValue } from '@/lib/standard-filters'
import PlanLineBreakdown from '../_lib/PlanLineBreakdown'
import { GoogleRatingChips, GoogleRatingDetail, ratingsText, useGoogleRatings } from '../_lib/googleRatings'

interface Rep {
  epay_salesperson: string
  storeops_name: string
  store: string
  tier: number
  kpis_met: number
  total_kpis: number
  premium_acts: number
  byod_acts: number
  upgrade_acts: number
  premium_comm: number
  byod_comm: number
  upgrade_comm: number
  acc_comm: number
  setup_fee_comm: number
  trade_in_comm: number
  acima_comm: number
  subtotal: number
  total_payout: number
  residual_installment_comm?: number   // multi-month installment pay from the RESIDUAL (raw_mi) engine
  installment_comm_sale?: number       // multi-month installment pay from the SALE-triggered ledger
                                       // (compute_sale_installments) — the one /commission-explain itemizes
  carrier_statement_comm?: number
  plan_comm?: number                    // configurable Commission Plan pay (non-Boost carriers, mig 059)
  plan_name?: string
  final_payout?: number                 // total_payout − chargeback_items deducted − ops chargebacks (backend)
  chargeback_deduction?: number
  ops_chargeback_deduction?: number     // POSTED ops-accountability chargebacks (retail-ops), commission-applied (net of overflow)
  ops_chargeback_lines?: {
    label: string; amount: number; reason: string; incident_date: string; store: string; status: string
    gross_amount?: number; covered_amount?: number | null; remainder?: number
    overflow?: 'payroll' | 'next_cycle' | null; overflow_period?: string | null
  }[]
}

const TABS = [
  { id: 'breakdown', label: '👥 Rep Breakdown' },
  { id: 'individual', label: '📄 Individual Rep' },
  { id: 'compensation', label: '💰 Compensation by Line' },
]

// ── PLAN-MODE (non-Boost) drill labels — mirrored from commission-explain/page.tsx so the same
// installment status/basis reads identically on both surfaces. Display only.
const PLAN_BASIS: Record<string, string> = {
  flat_per_unit: '$/unit', pct_gp: '% GP', pct_mrc: '% MRC',
  pct_price_over_cost: '% (price−cost)', flat: 'flat bonus',
}
const INST_REASON_LABEL: Record<string, string> = {
  paid: 'Paid', no_mi_match: 'Held — dealer not paid (no raw_mi row)',
  line_inactive: 'Held — line inactive', residual_not_received: 'Held — residual not received',
  activation_payment_missing: 'Held — no first-month payment', withheld: 'Held',
  held_stored: 'Held — stored row (see rep explain)',
}

export default function ReportsPage() {
  const { period } = usePeriod()
  const { carriers } = useAuth()
  const isBoost = carrierMode(carriers) === 'boost'   // non-Boost carriers pay via plans, not KPI tiers
  const [reps, setReps] = useState<Rep[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('breakdown')
  const [selectedRep, setSelectedRep] = useState('')
  // RULE FIVE (§3d) standard filter — period stays global (usePeriod), so the bar renders store(s)/market/
  // rep(s) multi. Options come from the already-org-scoped rep rows (pick-don't-type); `market` is stamped
  // on each row by the backend (store_mapping resolver).
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [cfg, setCfg] = useState<any>({})
  const [chargebacks, setChargebacks] = useState<any[]>([])
  const [drillComp, setDrillComp] = useState<string | null>(null)   // clicked commission component
  const [drillData, setDrillData] = useState<any>(null)
  const [drillBusy, setDrillBusy] = useState(false)
  const drillReq = useRef('')     // latest in-flight `${rep}|${period}` for the BOOST drill (INFO-4)
  // PLAN-MODE drill (non-Boost carriers). The Boost rows below drill through /commission-drill, which
  // replays raw_sales through the BOOST component classification — for a plan-mode rep those component
  // columns are zeroed by calculator.py, so that endpoint would show real transactions next to
  // misleading $0. Plan-mode therefore drills through /commission-explain, which IS carrier-mode aware
  // and is the same source the 🔬 "How was this calculated?" page already uses.
  const [planDrill, setPlanDrill] = useState<null | 'plan' | 'multimonth'>(null)
  const [explain, setExplain] = useState<any>(null)
  const [explainBusy, setExplainBusy] = useState(false)
  const explainReq = useRef('')   // latest in-flight `${rep}|${period}` — late replies are dropped

  useEffect(() => {
    api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setReps).catch(console.error).finally(() => setLoading(false))
    api(`/api/v1/commcalc/config/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setCfg).catch(console.error)
    api(`/api/v1/commcalc/chargebacks/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(setChargebacks).catch(console.error)
  }, [period])

  async function toggleChargeback(itemId: string, deduct: boolean) {
    setChargebacks(cbs => cbs.map(c => c.id === itemId ? { ...c, deduct } : c))
    try {
      await api(`/api/v1/commcalc/chargebacks/${itemId}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify({ deduct }),
      })
      // Refresh commissions so payout reflects the change
      const updated = await api(`/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      setReps(updated)
    } catch (e) { console.error(e) }
  }

  const repList  = useMemo(() => [...new Set(reps.map(r => r.epay_salesperson))].sort(), [reps])   // Individual-rep tab picker
  // Standard-bar options straight from the loaded (org-scoped) rows — stores/markets/reps present.
  const acc = { store: (r: Rep) => r.store, market: (r: Rep) => (r as any).market, rep: (r: Rep) => r.epay_salesperson }
  const opts = useMemo(() => optionsFromRows(reps, acc), [reps])   // eslint-disable-line react-hooks/exhaustive-deps
  // FILTERED set drives the breakdown + compensation tables, the header tiles, AND the CSV export (WYSIWYG).
  const filtered = useMemo(() => filterRows(reps, filt, acc), [reps, filt])   // eslint-disable-line react-hooks/exhaustive-deps
  const totalPayout = filtered.reduce((s, r) => s + (r.total_payout || 0), 0)
  const filterActive = isStandardFilterActive(filt)

  const currentRep = reps.find(r => r.epay_salesperson === selectedRep) || reps[0]
  // ── GOOGLE STORE RATING (owner 2026-08-06) — display-only, NON-money ───────────────────────────
  // ONE batched summary call for the reps currently on screen (the FILTERED set + whoever the
  // Individual tab is showing), never one call per row. Renders nothing at all until mod-people's
  // google-reviews endpoints are live, so today's page is byte-identical. Chips attach to the same
  // filtered rows the table and the export use, so all three can never disagree (RULE FIVE/FOUR).
  const ratingNames = useMemo(
    () => [...filtered.map(r => r.storeops_name || r.epay_salesperson),
           ...(currentRep ? [currentRep.storeops_name || currentRep.epay_salesperson] : [])],
    [filtered, currentRep])
  const { ratingsFor: googleFor, hasAny: hasGoogle } = useGoogleRatings(ratingNames)
  const currentRepName = currentRep ? (currentRep.storeops_name || currentRep.epay_salesperson) : ''
  // repLabel(row) → one export cell. Built ONLY from what is on screen; empty ⇒ the export column
  // disappears entirely (see commissionExport.ratingCol).
  const ratingByRep = useMemo(() => {
    if (!hasGoogle) return undefined
    const out: Record<string, string> = {}
    const add = (r?: Rep) => {
      if (!r) return
      const label = r.storeops_name || r.epay_salesperson
      const txt = ratingsText(googleFor(label))
      if (txt) out[label] = txt
    }
    filtered.forEach(add)
    add(currentRep)
    return Object.keys(out).length ? out : undefined
  }, [filtered, currentRep, hasGoogle, googleFor])
  // Show the Installment column only when a rep actually has multi-month / Total-carrier pay (keeps the
  // Boost view unchanged). residual_installment_comm + carrier_statement_comm are already inside Payout.
  const instOf = (r: Rep) => (r.residual_installment_comm || 0) + (r.carrier_statement_comm || 0)
  const hasInstallment = filtered.some(r => instOf(r) !== 0)

  // ── Gate-1 MINOR-2: multi-month installments come from TWO engines, and the plan drill itemizes ONE ──
  // `installment_comm_sale` = the SALE-triggered ledger (compute_sale_installments) — exactly what the
  // drill modal lists per device. `residual_installment_comm` = the raw_mi RESIDUAL engine, which pays off
  // carrier residual rows and therefore has no sale lines for that modal to show. The card used to print
  // the residual column above a modal telling the sale-triggered story, so on a residual-path tenant the
  // number and the explanation disagreed. Each POPULATED component now gets its own labelled row and the
  // clickable one is the component the modal is actually about.
  // DISPLAY ONLY — nothing stored changes: both columns are already summed into total_payout by the
  // backend (`base + inst + sale_inst`), and the modal's reconciliation strip still prints both.
  const instSale  = currentRep?.installment_comm_sale || 0        // itemized by the drill modal
  const instResid = currentRep?.residual_installment_comm || 0    // raw_mi residual engine — not itemized
  const showResidRow = instResid !== 0
  // the sale-triggered row also carries the ALWAYS-available click path, so it renders at $0 too — unless
  // the residual engine is the only payer, in which case that would be a second, meaningless $0 row.
  const showSaleRow  = instSale !== 0 || !showResidRow

  const COMP_LABEL: Record<string, string> = { premium: 'Premium Activations', byod: 'BYOD Activations', upgrade: 'Device Upgrades', accessories: 'Accessories', setup: 'Setup Fees', acima: 'ACIMA Lease' }

  // ── EXPORTS (owner bug 2026-08-04: “exporting ONE employee sent EVERY employee’s pay”) ─────────
  // Root cause + the WYSIWYG contract live in _lib/commissionExport.ts, which is a PURE module
  // precisely so the scope rule ("individual tab exports the selected rep ALONE") can be proven by
  // frontend/tools/commission-export-proof.mjs instead of only argued in a comment.
  // READ-PATH ONLY — no rate, tier, plan rule or stored payout is touched.
  const exportInput = (): CommissionExportInput => ({
    tab: tab as CommissionTab, period, isBoost, reps, filtered, currentRep: currentRep || null,
    filt, cfg, chargebacks, hasInstallment, ratingByRep,
  })
  const buildPayload = (): ExportPayload => buildCommissionExport(exportInput())
  function downloadCSV() {
    const p = buildPayload()
    const a = document.createElement('a')
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(payloadToCsv(p))
    a.download = `${p.filename}.csv`; a.click()
  }
  // Cheap title for the Send modal's header (buildPayload() itself only runs on click).
  const exportTitle = tab === 'individual'
    ? `Commission Statement — ${currentRep ? repLabel(currentRep) : 'no rep selected'}`
    : tab === 'compensation' ? 'Compensation by Line' : 'Rep Commission Report'

  function openDrill(comp: string) {
    setDrillComp(comp)
    const rep = currentRep?.storeops_name || currentRep?.epay_salesperson || ''
    // Gate-1 INFO-4 — the cache used to be keyed by REP ONLY, so switching the period and reopening the
    // same rep's drill replayed the PREVIOUS period's transactions under the new period's header. Key +
    // tag are now `rep|period`, mirroring the plan drill (explainReq / explainFresh).
    if (drillData && drillData._rep === rep && drillData._period === period) return   // already loaded
    const key = `${rep}|${period}`
    drillReq.current = key                          // only the LATEST request may land
    setDrillData(null); setDrillBusy(true)
    api(`/api/v1/commcalc/commission-drill?org_id=${ORG_ID}&period=${encodeURIComponent(period)}&rep=${encodeURIComponent(rep)}`)
      .then((d: any) => { if (drillReq.current === key) setDrillData({ ...d, _rep: rep, _period: period }) })
      .catch(e => { if (drillReq.current === key) setDrillData({ error: String(e?.message || e), _rep: rep, _period: period }) })
      .finally(() => { if (drillReq.current === key) setDrillBusy(false) })
  }
  const drillStyle = { cursor: 'pointer' } as React.CSSProperties

  // ── PLAN-MODE drill (display-only; no calc, no writes) ───────────────────────────────────────────
  // org_id is NOT pinned here: client.ts appends the ACTING org as a query param (contract §2), so a
  // super-admin acting as a tenant drills that tenant's data, never the house org's.
  // the rep the drill is about — ALSO the freshness key for the async response (see MINOR-1 below)
  const drillRep = currentRep?.storeops_name || currentRep?.epay_salesperson || ''
  function openPlanDrill(which: 'plan' | 'multimonth') {
    setPlanDrill(which)
    const rep = drillRep
    if (!rep) return
    // cache per rep+period — but NEVER cache a failure, so a retry actually re-fetches
    if (explain && !explain.error && explain._rep === rep && explain._period === period) return
    const key = `${rep}|${period}`
    explainReq.current = key                       // only the LATEST request may land
    setExplain(null); setExplainBusy(true)
    api(`/api/v1/commcalc/commission-explain?period=${encodeURIComponent(period)}&rep=${encodeURIComponent(rep)}`)
      .then((d: any) => { if (explainReq.current === key) setExplain({ ...d, _rep: rep, _period: period }) })
      .catch(e => { if (explainReq.current === key) setExplain({ error: String(e?.message || e), _rep: rep, _period: period }) })
      .finally(() => { if (explainReq.current === key) setExplainBusy(false) })
  }
  // Gate-1 MINOR-1 — stale-response race: open rep A, close mid-flight, open rep B; a late reply for A
  // must NEVER render under B's header. Every render path reads the response only when it is TAGGED for
  // the CURRENT rep+period. The error path uses the same gate, so an error for the current rep still
  // shows (with Retry) while a stale one cannot. explainReq drops the late reply at the source too, so
  // the modal keeps showing "Loading…" instead of flashing an empty state while the fresh call is in
  // flight.
  const explainFresh = explain && explain._rep === drillRep && explain._period === period ? explain : null
  const explainOk  = explainFresh && !explainFresh.error ? explainFresh : null
  const explainPc  = explainOk?.plan_component || null
  const explainMm  = explainOk?.multimonth_component || null
  const explainRec = explainOk?.reconciliation || null
  // Gate-1 INFO-4 — the BOOST drill gets the SAME rep+period render gate (declared here because it reuses
  // `drillRep` above). openDrill() tags its payload with the identical rep expression, so for a fresh
  // fetch drillFresh === drillData and the Boost modal renders exactly what it rendered before; what it
  // can no longer do is render a payload fetched for a DIFFERENT period (or rep).
  const drillFresh = drillData && drillData._rep === drillRep && drillData._period === period ? drillData : null
  const drillOk    = drillFresh && !drillFresh.error ? drillFresh : null
  // per-rule matched sale lines (same shape commission-explain/page.tsx maps into planRows)
  const planLineRows = useMemo(() => {
    const out: any[] = []
    for (const r of (explainPc?.rules || [])) for (const l of (r.lines || []))
      out.push({ rule: r.label, basis: PLAN_BASIS[r.payout_kind] || r.payout_kind, date: l.date,
        trans_id: l.trans_id, product: l.product, contract_type: l.contract_type,
        ext_price: l.ext_price, gp: l.gp, amount: l.flat_once ? null : l.amount,
        // carried for the per-category UNIT count and the "matched but not paid" marker in the
        // grouped drill-down — same fields commission-explain already reads. Display only.
        qualifies: l.qualifies !== false, suppressed: !!l.suppressed,
        suppressed_reason: l.suppressed_reason || '', would_have_paid: l.would_have_paid ?? 0 })
    return out
  }, [explainPc])
  // rules that matched NOTHING — the honest "why is this $0" answer for a plan-mode rep
  const planDeadRules = useMemo(
    () => (explainPc?.rules || []).filter((r: any) => !(r.matched_lines || 0)), [explainPc])
  // per-device installments with month / status / hold reason
  const instLineRows = useMemo(() => {
    const out: any[] = []
    for (const d of (explainMm?.devices || [])) for (const i of (d.installments || []))
      // ONE consistent label (owner 2026-07-27): DEVICE — RATE PLAN — MRC, resolved by the engine, so
      // this drill never shows the phone on one row and the rate plan on the next.
      out.push({ imei: d.imei, mdn: d.mdn, product: i.label || d.label || d.product,
        device_category: i.device_category || d.device_category, month_index: i.month_index,
        pay_period: i.pay_period, status_label: INST_REASON_LABEL[i.hold_reason] || i.status,
        hold_detail: i.hold_detail, amount: i.amount, withheld_amount: i.withheld_amount,
        mrc_at_pay: i.mrc_at_pay, ma_says_paid: d.ma_says_paid, paid: i.status === 'paid' })
    return out
  }, [explainMm])

  function TierBadge({ tier }: { tier: number }) {
    const pct = Math.round((tier || 0) * 100)
    const cls = pct >= 100 ? 'badge-green' : pct >= 75 ? 'badge-amber' : 'badge-red'
    return <span className={`badge ${cls}`}>{pct}%</span>
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Rep Commission Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {filtered.length}{filterActive ? ` of ${reps.length}` : ''} reps · Total: <strong style={{ color: 'var(--accent)' }}>{fmt(totalPayout)}</strong>
          </p>
        </div>
        {/* WYSIWYG (§3c): every format renders from buildPayload() — the FILTERED rows on the list tabs,
            the SELECTED rep alone on Individual Rep. Send takes `exportPayload` (in-browser render →
            /notify/send-file), NOT the old server report-key path (reportKey "commissions", period
            only) which re-queried the whole org and emailed every rep's pay. */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn btn-secondary" onClick={downloadCSV}>📥 CSV</button>
          <ExportButtons payload={buildPayload} compact />
          <SendReportButton exportPayload={buildPayload} title={exportTitle} compact />
        </div>
      </div>
      {tab === 'individual' && (
        <div style={{ fontSize: 12, color: 'var(--text2)', margin: '-10px 0 14px' }}>
          🔒 Exports on this tab contain <b>only {currentRep ? repLabel(currentRep) : 'the selected rep'}</b>.
          {' '}Switch to Rep Breakdown to export the whole filtered team.
        </div>
      )}

      {/* RULE FIVE (§3d) standard bar — ABOVE the tabs so the active filter (which drives the always-visible
          header total AND both the Breakdown and Compensation tables) is always visible + clearable, on any tab. */}
      <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false }}
        storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps}
        repLabel="Reps…"
        right={<span style={{ fontSize: 13, color: 'var(--text2)', alignSelf: 'center' }}>{filtered.length} of {reps.length} rows</span>} />

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--surface2)', padding: 4, borderRadius: 10, width: 'fit-content' }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className="btn" style={{
            background: tab === t.id ? 'white' : 'transparent',
            color: tab === t.id ? 'var(--accent)' : 'var(--text2)',
            boxShadow: tab === t.id ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            fontSize: 13,
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Rep Breakdown */}
      {tab === 'breakdown' && (
        <div>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Rep</th>
                  <th>Store</th>
                  <th>Tier</th>
                  <th>KPIs</th>
                  <th>PA</th><th>BA</th><th>UA</th>
                  <th style={{ textAlign: 'right' }}>ACC GP</th>
                  <th style={{ textAlign: 'right' }}>ACIMA</th>
                  {hasInstallment && <th style={{ textAlign: 'right' }} title="Multi-month / Total-carrier installment pay (already inside Payout)">Installment</th>}
                  <th style={{ textAlign: 'right' }}>Subtotal</th>
                  <th style={{ textAlign: 'right' }}>Payout</th>
                  {hasGoogle && <th title="Google rating vs target, per store this rep works at — display only, not part of pay">Google</th>}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={(hasInstallment ? 12 : 11) + (hasGoogle ? 1 : 0)} style={{ textAlign: 'center', padding: 40 }}>
                    <div className="spinner" style={{ margin: '0 auto' }} />
                  </td></tr>
                ) : filtered.length === 0 ? (
                  <tr><td colSpan={(hasInstallment ? 12 : 11) + (hasGoogle ? 1 : 0)} style={{ textAlign: 'center', color: 'var(--text3)', padding: 40 }}>
                    No data. Upload files and run calculation.
                  </td></tr>
                ) : filtered.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <button
                        style={{ background: 'none', border: 'none', cursor: 'pointer', fontWeight: 500,
                          color: 'var(--accent)', textDecoration: 'underline', fontSize: 13, padding: 0 }}
                        onClick={() => { setSelectedRep(r.epay_salesperson); setTab('individual') }}
                      >
                        {r.storeops_name || r.epay_salesperson}
                      </button>
                      {' '}
                      <a href={`/commcalc/commission-explain?rep=${encodeURIComponent(r.storeops_name || r.epay_salesperson)}`}
                        title="How was this commission calculated? (plan + multi-month drill-down)"
                        style={{ fontSize: 11, textDecoration: 'none' }}>🔬</a>
                    </td>
                    <td style={{ color: 'var(--text3)', fontSize: 12 }}>{r.store?.substring(0, 25)}</td>
                    <td><TierBadge tier={r.tier} /></td>
                    <td style={{ fontSize: 12 }}>{r.kpis_met}/{r.total_kpis}</td>
                    <td>{r.premium_acts}</td>
                    <td>{r.byod_acts}</td>
                    <td>{r.upgrade_acts}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(r.acc_comm)}</td>
                    <td style={{ textAlign: 'right', color: r.acima_comm > 0 ? '#7c3aed' : 'var(--text3)' }}>
                      {r.acima_comm > 0 ? fmt(r.acima_comm) : '—'}
                    </td>
                    {hasInstallment && <td style={{ textAlign: 'right', color: instOf(r) ? '#0369a1' : 'var(--text3)' }}>{instOf(r) ? fmt(instOf(r)) : '—'}</td>}
                    <td style={{ textAlign: 'right' }}>{fmt(r.subtotal)}</td>
                    <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.total_payout)}</td>
                    {hasGoogle && <td><GoogleRatingChips list={googleFor(r.storeops_name || r.epay_salesperson)} compact /></td>}
                  </tr>
                ))}
              </tbody>
              {filtered.length > 0 && (
                <tfoot>
                  <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                    <td colSpan={9} style={{ textAlign: 'right', paddingRight: 8, color: 'var(--text2)' }}>Total:</td>
                    {hasInstallment && <td style={{ textAlign: 'right', color: '#0369a1' }}>{fmt(filtered.reduce((s, r) => s + instOf(r), 0))}</td>}
                    <td style={{ textAlign: 'right', color: 'var(--text2)' }}>{fmt(filtered.reduce((s, r) => s + (r.subtotal || 0), 0))}</td>
                    <td style={{ textAlign: 'right', color: 'var(--accent)' }}>
                      {fmt(filtered.reduce((s, r) => s + r.total_payout, 0))}
                    </td>
                    {hasGoogle && <td />}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </div>
      )}

      {/* Individual Rep */}
      {tab === 'individual' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
            <select className="select" value={selectedRep} onChange={e => setSelectedRep(e.target.value)}>
              <option value="">Select rep...</option>
              {repList.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
            {currentRep && (
              <a className="btn btn-secondary" style={{ textDecoration: 'none' }}
                href={`/commcalc/commission-explain?rep=${encodeURIComponent(currentRep.storeops_name || currentRep.epay_salesperson)}`}
                title="Plan + multi-month drill-down: which assignment, per-rule lines, installment gates & MA cross-reference">
                🔬 How was this calculated?
              </a>
            )}
            {/* This rep's Google store rating(s) — chips sit beside the person, exactly like on the
                ranking/review scorecards. Renders nothing when there is no rating data. */}
            <GoogleRatingChips list={googleFor(currentRepName)} />
          </div>

          {currentRep ? (
            <div>
              {/* Summary cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 20 }}>
                <div className="card" style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--accent)' }}>{fmt(currentRep.total_payout)}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 12 }}>Total Payout</div>
                </div>
                <div className="card" style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 28, fontWeight: 700 }}>{fmt(currentRep.subtotal)}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 12 }}>Subtotal (pre-tier)</div>
                </div>
                {isBoost ? (
                  <div className="card" style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 28, fontWeight: 700 }}>
                      <span style={{ color: currentRep.tier >= 1 ? '#16a34a' : currentRep.tier >= 0.75 ? '#d97706' : '#dc2626' }}>
                        {Math.round((currentRep.tier || 0) * 100)}%
                      </span>
                    </div>
                    <div style={{ color: 'var(--text2)', fontSize: 12 }}>Tier Multiplier · {currentRep.kpis_met}/{currentRep.total_kpis} KPIs</div>
                  </div>
                ) : (
                  <div className="card" style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: 16, fontWeight: 700, marginTop: 6 }}>{currentRep.plan_name || '— no plan —'}</div>
                    <div style={{ color: 'var(--text2)', fontSize: 12 }}>Commission Plan</div>
                  </div>
                )}
              </div>

              {/* Non-Boost carriers: pay comes from the assigned Commission Plan, not Boost line items */}
              {!isBoost && (
                <div className="card" style={{ padding: 16, marginBottom: 16 }}>
                  <div style={{ fontWeight: 600, marginBottom: 10 }}>Plan‑based Payout</div>
                  <table>
                    <tbody>
                      <tr><td>Commission Plan</td><td style={{ textAlign: 'right', fontWeight: 600 }}>{currentRep.plan_name || '— none assigned —'}</td></tr>
                      {/* Clickable: same affordance as the Boost line items (🔍 + rs-clickable + pointer).
                          Amounts shown are UNCHANGED — the click only opens a read-only breakdown. */}
                      <tr className="rs-clickable" style={drillStyle} onClick={() => openPlanDrill('plan')}
                        title="Show the per-rule sale lines this Commission Plan paid on">
                        <td>🔍 Plan commission</td>
                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.plan_comm ?? 0)}</td>
                      </tr>
                      {/* SALE-TRIGGERED installments — the component the drill modal itemizes, so this is
                          the row that opens it. Rendered even at $0 (unless the residual engine is the only
                          payer) so there is ALWAYS a click path — a $0 here is exactly the case an operator
                          needs explained (every month held / no schedule / not calculated yet). */}
                      {showSaleRow && (
                        <tr className="rs-clickable" style={drillStyle} onClick={() => openPlanDrill('multimonth')}
                          title="Sale-triggered installment ledger — each device's M1–M6 with gate status and hold reason">
                          <td>🔍 Multi‑month installments{showResidRow ? ' (sale‑triggered)' : ''}</td>
                          <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(instSale)}</td>
                        </tr>
                      )}
                      {/* RESIDUAL (raw_mi) installments — a different engine, paid off carrier residual rows
                          rather than sale lines, so the sale-triggered modal cannot itemize it. It gets its
                          own labelled row instead of being displayed under that modal's story. */}
                      {showResidRow && (
                        <tr title="Paid by the residual (raw_mi) engine — per-device detail is on the full explain page">
                          <td>Multi‑month installments (residual · raw_mi)</td>
                          <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(instResid)}</td>
                        </tr>
                      )}
                      <tr style={{ fontWeight: 700 }}><td>Total Payout</td><td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt(currentRep.total_payout)}</td></tr>
                    </tbody>
                  </table>
                  {showResidRow && (
                    <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
                      Residual (raw‑mi) installments are paid off carrier residual rows, not off sale lines, so
                      the sale‑triggered breakdown can’t itemize them —{' '}
                      <a href={`/commcalc/commission-explain?rep=${encodeURIComponent(drillRep)}`} style={{ color: 'var(--accent)' }}>
                        open the full explain page
                      </a>{' '}for their per‑device detail.
                    </div>
                  )}
                  {!currentRep.plan_name && (
                    <div style={{ fontSize: 12, color: '#dc2626', marginTop: 8 }}>
                      No plan assigned to this rep — they calculate to $0. Assign one on{' '}
                      <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Commission Plans</a>.
                    </div>
                  )}
                </div>
              )}

              {/* Line items table (Boost KPI‑tier breakdown) */}
              {isBoost && (
              <div className="card" style={{ padding: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th style={{ textAlign: 'right' }}>Count</th>
                      <th style={{ textAlign: 'right' }}>Rate</th>
                      <th style={{ textAlign: 'right' }}>Commission</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('premium')}>
                      <td>🔍 Premium Activations</td>
                      <td style={{ textAlign: 'right' }}>{currentRep.premium_acts}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.premium_flat || 0)}/act</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.premium_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('byod')}>
                      <td>🔍 BYOD Activations</td>
                      <td style={{ textAlign: 'right' }}>{currentRep.byod_acts}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt((cfg.byod_flat || 0) + (cfg.byod_extra_spiff || 0))}/act</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.byod_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('upgrade')}>
                      <td>🔍 Device Upgrades</td>
                      <td style={{ textAlign: 'right' }}>{currentRep.upgrade_acts}</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.upgrade_flat || 0)}/act</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.upgrade_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('accessories')}>
                      <td>🔍 Accessories (10% GP)</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>GP</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>10% GP</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.acc_comm)}</td>
                    </tr>
                    <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('setup')}>
                      <td>🔍 Setup Fees (10% GP)</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>GP</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>10% GP</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.setup_fee_comm)}</td>
                    </tr>
                    <tr>
                      <td>Trade-In SPIFF</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>—</td>
                      <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.trade_in_spiff || 0)}/trade</td>
                      <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(currentRep.trade_in_comm)}</td>
                    </tr>
                    {(currentRep.acima_comm || 0) > 0 && (
                      <tr className="rs-clickable" style={drillStyle} onClick={() => openDrill('acima')}>
                        <td>🔍 ACIMA Lease SPIFF</td>
                        <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>
                          {Math.round((currentRep.acima_comm || 0) / (cfg.acima_spiff || 25))} txns
                        </td>
                        <td style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 12 }}>{fmt(cfg.acima_spiff || 25)} each</td>
                        <td style={{ textAlign: 'right', fontWeight: 600, color: '#7c3aed' }}>{fmt(currentRep.acima_comm)}</td>
                      </tr>
                    )}
                    <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                      <td colSpan={3}>Subtotal</td>
                      <td style={{ textAlign: 'right' }}>{fmt(currentRep.subtotal)}</td>
                    </tr>
                    <tr style={{ fontWeight: 700 }}>
                      <td colSpan={3}>× {Math.round((currentRep.tier || 0) * 100)}% Tier</td>
                      <td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt(currentRep.total_payout)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              )}

              {/* Chargeback review */}
              {(() => {
                const repCbs = chargebacks.filter(cb => cb.epay_salesperson === currentRep.epay_salesperson)
                if (!repCbs.length) return null
                const deducted = repCbs.filter(c => c.deduct).reduce((s, c) => s + (c.amount || 0), 0)
                return (
                  <div className="card" style={{ padding: 0, marginTop: 20, border: '1px solid #fca5a5' }}>
                    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, background: '#fef2f2', color: '#991b1b' }}>
                      ⚠️ Potential Chargebacks — {repCbs.length} items · Toggle to deduct from payout
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left' }}>Source</th>
                          <th style={{ textAlign: 'left' }}>Description</th>
                          <th style={{ textAlign: 'left' }}>MDN/IMEI</th>
                          <th style={{ textAlign: 'right' }}>Amount</th>
                          <th style={{ textAlign: 'center' }}>Deduct?</th>
                        </tr>
                      </thead>
                      <tbody>
                        {repCbs.map(cb => (
                          <tr key={cb.id} style={{ background: cb.deduct ? '#fef2f2' : undefined }}>
                            <td style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text3)' }}>{cb.source}</td>
                            <td style={{ fontSize: 12 }}>{cb.description}</td>
                            <td style={{ fontSize: 11, color: 'var(--text3)' }}>{cb.mdn || cb.imei || '—'}</td>
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmt(cb.amount)}</td>
                            <td style={{ textAlign: 'center' }}>
                              <input type="checkbox" checked={!!cb.deduct}
                                onChange={e => toggleChargeback(cb.id, e.target.checked)}
                                style={{ width: 18, height: 18, cursor: 'pointer' }} />
                            </td>
                          </tr>
                        ))}
                        <tr style={{ fontWeight: 700, background: 'var(--surface2)' }}>
                          <td colSpan={3}>Total Deducted</td>
                          <td style={{ textAlign: 'right', color: 'var(--red)' }}>−{fmt(deducted)}</td>
                          <td></td>
                        </tr>
                        <tr style={{ fontWeight: 700 }}>
                          <td colSpan={3}>Final Payout (after all deductions)</td>
                          <td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt((currentRep.total_payout || 0) - deducted - (currentRep.ops_chargeback_deduction || 0))}</td>
                          <td></td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )
              })()}

              {/* Ops-accountability chargebacks (POSTED, read-only) — retail-ops' commcalc.ops_chargeback,
                  commission-applied. Deducted from this person's commission for the period. Posting/waiving
                  happens on the DM Verify page (retail-ops), NOT here — this is a read-only statement line. */}
              {(() => {
                const lines = currentRep.ops_chargeback_lines || []
                if (!lines.length) return null
                const opsTotal = currentRep.ops_chargeback_deduction ?? lines.reduce((s, l) => s + (l.amount || 0), 0)
                const hasCbItems = chargebacks.some(cb => cb.epay_salesperson === currentRep.epay_salesperson)
                return (
                  <div className="card" style={{ padding: 0, marginTop: 20, border: '1px solid #fca5a5' }}>
                    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, background: '#fef2f2', color: '#991b1b' }}>
                      🔻 Ops Accountability Chargebacks — {lines.length} POSTED · deducted from commission
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left' }}>Ops chargeback</th>
                          <th style={{ textAlign: 'center' }}>Status</th>
                          <th style={{ textAlign: 'right' }}>Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lines.map((l, i) => (
                          <tr key={i}>
                            <td style={{ fontSize: 12 }}>{l.label}</td>
                            <td style={{ textAlign: 'center' }}>
                              <span className="badge badge-red" style={{ textTransform: 'uppercase', fontSize: 10 }}>{l.status || 'posted'}</span>
                            </td>
                            <td style={{ textAlign: 'right', fontWeight: 600 }}>−{fmt(l.amount)}</td>
                          </tr>
                        ))}
                        <tr style={{ fontWeight: 700, background: 'var(--surface2)' }}>
                          <td colSpan={2}>Total ops chargebacks deducted</td>
                          <td style={{ textAlign: 'right', color: 'var(--red)' }}>−{fmt(opsTotal)}</td>
                        </tr>
                        {!hasCbItems && (
                          <tr style={{ fontWeight: 700 }}>
                            <td colSpan={2}>Final Payout (after ops chargebacks)</td>
                            <td style={{ textAlign: 'right', color: 'var(--accent)' }}>{fmt(currentRep.final_payout ?? ((currentRep.total_payout || 0) - opsTotal))}</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                    <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text3)' }}>
                      Posted or waived by management on the DM Verify page — read-only here.
                    </div>
                  </div>
                )
              })()}

              {/* GOOGLE STORE RATINGS for this rep — the fuller per-store view (rating vs target, review
                  count, any open action plan) with Google's recent reviews collapsed behind a toggle.
                  Sits BELOW the pay statement on purpose: it is context, not compensation, and it changes
                  no number above it. Renders nothing at all when the tenant has no Google data. */}
              <div style={{ marginTop: 20 }}>
                <GoogleRatingDetail repName={currentRepName}
                  title={`Google store ratings — ${currentRep.storeops_name || currentRep.epay_salesperson}`} />
              </div>
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
              Select a rep to view their commission breakdown
            </div>
          )}
        </div>
      )}

      {/* Compensation by Line */}
      {tab === 'compensation' && (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>Rep</th>
                <th style={{ textAlign: 'right' }}>Premium</th>
                <th style={{ textAlign: 'right' }}>BYOD</th>
                <th style={{ textAlign: 'right' }}>Upgrades</th>
                <th style={{ textAlign: 'right' }}>Accessories</th>
                <th style={{ textAlign: 'right' }}>Setup Fees</th>
                <th style={{ textAlign: 'right' }}>Trade-Ins</th>
                <th style={{ textAlign: 'right' }}>ACIMA</th>
                <th style={{ textAlign: 'right' }}>Subtotal</th>
                <th style={{ textAlign: 'right' }}>Payout</th>
                {hasGoogle && <th title="Google rating vs target, per store this rep works at — display only, not part of pay">Google</th>}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 500 }}>{r.storeops_name || r.epay_salesperson}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.premium_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.byod_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.upgrade_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.acc_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.setup_fee_comm)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.trade_in_comm)}</td>
                  <td style={{ textAlign: 'right', color: r.acima_comm > 0 ? '#7c3aed' : 'var(--text3)' }}>
                    {r.acima_comm > 0 ? fmt(r.acima_comm) : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.subtotal)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.total_payout)}</td>
                  {hasGoogle && <td><GoogleRatingChips list={googleFor(r.storeops_name || r.epay_salesperson)} compact /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Commission component drill-down — the exact transactions behind a paid-out line */}
      {drillComp && (() => {
        const b = drillOk ? drillOk[drillComp] : null   // drillOk = drillData, gated on rep+period (INFO-4)
        const moneyBucket = drillComp === 'accessories' || drillComp === 'setup'
        return (
          <div onClick={() => setDrillComp(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(900px,97vw)', maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 12px 40px rgba(0,0,0,0.25)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>{COMP_LABEL[drillComp]} · {currentRep?.storeops_name || currentRep?.epay_salesperson}</div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>{period}{b ? ` · ${moneyBucket ? `${b.count} line${b.count === 1 ? '' : 's'} · ${fmt(b.sales)} sales · ${fmt(b.gp)} GP` : `${b.count} transaction${b.count === 1 ? '' : 's'}`}` : ''}{drillFresh?.source === 'daily_sales_feed' ? ' · source: daily feed' : ''}</div>
                </div>
                <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDrillComp(null)}>✕</button>
              </div>
              {drillBusy ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading transactions…</div>
              ) : drillFresh?.error ? (
                <div style={{ padding: 20, color: '#dc2626', fontSize: 13 }}>❌ {drillFresh.error}</div>
              ) : !b || b.items.length === 0 ? (
                <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No transactions found for this component in {period}.</div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead><tr style={{ background: 'var(--surface2)' }}>
                      {['Date', 'Trans ID', 'Product', drillComp === 'acima' ? 'Tender' : 'Contract', 'MDN', 'Price', 'GP'].map(h =>
                        <th key={h} style={{ textAlign: h === 'Price' || h === 'GP' ? 'right' : 'left', padding: '5px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {b.items.map((it: any, i: number) => (
                        <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                          <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>{it.date}</td>
                          <td style={{ padding: '5px 8px', fontFamily: 'monospace' }}>{it.trans_id}</td>
                          <td style={{ padding: '5px 8px' }}>{it.product || '—'}</td>
                          <td style={{ padding: '5px 8px' }}>{drillComp === 'acima' ? (it.tender_type || '—') : (it.contract_type || '—')}</td>
                          <td style={{ padding: '5px 8px' }}>{it.mdn || '—'}</td>
                          <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(it.ext_price)}</td>
                          <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(it.gp)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )
      })()}

      {/* PLAN-MODE drill-down (non-Boost carriers) — DISPLAY ONLY: reads /commission-explain, writes
          nothing, changes no payout number. Deliberately a SEPARATE modal from the Boost one above so
          the Boost path stays byte-identical. */}
      {planDrill && (() => {
        const rep = drillRep
        const isPlanTab = planDrill === 'plan'
        const liveTotal = explainPc ? (explainPc.total_payout ?? 0) : null
        const storedPlan = explainRec ? (explainRec.plan_comm ?? 0) : null
        const drifted = storedPlan !== null && liveTotal !== null && Math.abs(storedPlan - liveTotal) >= 0.01
        const mmTotals = explainMm?.totals || {}
        return (
          <div onClick={() => setPlanDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(1000px,97vw)', maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 12px 40px rgba(0,0,0,0.25)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>
                    {isPlanTab ? 'Plan commission' : 'Multi‑month installments'} · {rep || '— no rep selected —'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                    {period}
                    {isPlanTab
                      ? (explainPc?.plan_name ? ` · plan: ${explainPc.plan_name} · ${planLineRows.length} matched line${planLineRows.length === 1 ? '' : 's'}` : '')
                      : (explainMm ? ` · ${mmTotals.paid ?? 0} paid · ${mmTotals.withheld ?? 0} held` : '')}
                  </div>
                </div>
                <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setPlanDrill(null)}>✕</button>
              </div>

              {!rep ? (
                <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>Select a rep first.</div>
              ) : explainBusy ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading breakdown…</div>
              ) : explainFresh?.error ? (
                <div style={{ padding: 20, color: '#dc2626', fontSize: 13 }}>
                  ❌ {explainFresh.error}
                  <div style={{ marginTop: 10 }}>
                    <button className="btn btn-secondary" style={{ padding: '2px 10px' }}
                      onClick={() => openPlanDrill(planDrill)}>Retry</button>
                  </div>
                </div>
              ) : !explainOk ? (
                <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No breakdown returned for this rep.</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {explainOk.note && (
                    <div style={{ fontSize: 12, color: '#b45309' }}>{explainOk.note}</div>
                  )}

                  {isPlanTab ? (
                    <>
                      {explainPc?.plan_name ? (
                        <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                          Plan <b style={{ color: 'var(--text)' }}>{explainPc.plan_name}</b>
                          {explainPc.assignment ? <> attached via the <b>{explainPc.assignment.scope}</b> assignment
                            {explainPc.assignment.scope_value ? <> = <b>“{explainPc.assignment.scope_value}”</b></> : null}</> : null}.
                          {' '}Subtotal {fmt((explainPc.base_payout || 0) + (explainPc.tiered_payout || 0))} × tier{' '}
                          {explainPc.tier_multiplier ?? 1} = <b style={{ color: 'var(--accent)' }}>{fmt(explainPc.total_payout || 0)}</b>
                          {explainPc.qualifying_units != null ? ` · ${explainPc.qualifying_units} qualifying unit(s)` : ''}
                        </div>
                      ) : (
                        <div style={{ fontSize: 13, color: '#dc2626' }}>
                          No Commission Plan attached to this rep → $0 on the plan component. Assign one on{' '}
                          <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Commission Plans</a>.
                        </div>
                      )}

                      {(explainOk.zero_explanation?.length > 0) && (
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--text2)', lineHeight: 1.7 }}>
                          {explainOk.zero_explanation.map((z: string, i: number) => <li key={i}>{z}</li>)}
                        </ul>
                      )}

                      {/* OWNER 2026-08-04: date → numeric trans-id order, every line of a transaction
                          together with its own subtotal, and a category (plan-rule) breakdown/filter.
                          Shared with commission-explain so both drill-downs read identically. The
                          amounts are the engine's own line amounts — display only. */}
                      {planLineRows.length > 0 ? (
                        <PlanLineBreakdown rows={planLineRows} compact />
                      ) : (
                        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
                          {explainPc?.plan_name
                            ? `Plan attached, but no rule matched a sale line in ${period}.`
                            : `No plan-mode line detail for ${rep} in ${period}.`}
                        </div>
                      )}

                      {planDeadRules.length > 0 && (
                        <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                          <div style={{ fontWeight: 600, marginBottom: 4 }}>Rules that matched nothing</div>
                          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
                            {planDeadRules.map((r: any, i: number) => (
                              <li key={i}>
                                <b>{r.label || '(unnamed rule)'}</b> — expects {r.match_field || 'any'} {r.match_op || 'equals'}
                                {' '}“{r.match_value ?? ''}” ({PLAN_BASIS[r.payout_kind] || r.payout_kind})
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      {explainMm?.note && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{explainMm.note}</div>}
                      {instLineRows.length > 0 ? (
                        <div style={{ overflowX: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                            <thead><tr style={{ background: 'var(--surface2)' }}>
                              {['IMEI', 'Device — Rate plan', 'Category', 'Month', 'Pay period', 'Status / hold reason', 'MA says paid', 'Paid $', 'Held $', 'MRC'].map(h =>
                                <th key={h} style={{ textAlign: ['Paid $', 'Held $', 'MRC'].includes(h) ? 'right' : 'left', padding: '5px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>)}
                            </tr></thead>
                            <tbody>
                              {instLineRows.map((r: any, i: number) => (
                                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                                  <td style={{ padding: '5px 8px', fontFamily: 'monospace' }}>{r.imei || r.mdn || '—'}</td>
                                  <td style={{ padding: '5px 8px' }} title={r.product || ''}>{r.product || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{r.device_category || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>M{r.month_index}</td>
                                  <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>{r.pay_period || '—'}</td>
                                  <td style={{ padding: '5px 8px', color: r.paid ? 'var(--green)' : 'var(--red)' }}>
                                    {r.status_label}{r.hold_detail ? ` · ${r.hold_detail}` : ''}
                                  </td>
                                  <td style={{ padding: '5px 8px' }}>{r.ma_says_paid ? 'yes' : 'no'}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 600 }}>{fmt(r.amount)}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{r.withheld_amount == null ? '—' : fmt(r.withheld_amount)}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{r.mrc_at_pay == null ? '—' : fmt(r.mrc_at_pay)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
                          No sale-triggered multi‑month installments for {rep} in {period}
                          {explainMm && !explainMm.schedules ? ' — no active installment schedule is configured for this org.' : '.'}
                        </div>
                      )}
                    </>
                  )}

                  {/* Stored-vs-live honesty strip: /commission-explain computes LIVE from the current
                      config, while the card above shows what the last Calculate STORED. */}
                  <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, fontSize: 12, color: 'var(--text2)' }}>
                    {explainRec ? (
                      <>
                        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                          <span>Stored by last Calculate — Plan: <b>{fmt(explainRec.plan_comm)}</b></span>
                          <span>Sale installments: <b>{fmt(explainRec.installment_comm_sale)}</b></span>
                          <span>Residual (raw_mi): <b>{fmt(explainRec.residual_installment_comm)}</b></span>
                          <span>Total: <b style={{ color: 'var(--accent)' }}>{fmt(explainRec.total_payout)}</b></span>
                        </div>
                        {drifted && (
                          <div style={{ color: '#b45309', marginTop: 6 }}>
                            ⚠ The breakdown above is computed live from the CURRENT configuration ({fmt(liveTotal || 0)}),
                            which differs from the stored {fmt(storedPlan || 0)} written by the last Calculate.
                            Run Calculate for {period} to store the new numbers.
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{ color: '#b45309' }}>
                        No plan-mode calculation stored for {period} — the breakdown above is computed live
                        from the current configuration. Run Calculate after the config is in place to store it.
                      </div>
                    )}
                    <div style={{ marginTop: 6 }}>
                      <a href={`/commcalc/commission-explain?rep=${encodeURIComponent(rep)}`} style={{ color: 'var(--accent)' }}>
                        🔬 Open the full explain page (assignment trace, MA cross-reference, Excel/PDF export) →
                      </a>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )
      })()}
    </div>
  )
}
