'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, ORG_ID, fmt, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'
import ScreenLink from '@/components/ScreenLink'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// ACCESSORY TARGET ALLOCATION (index §61, owner ask 2026-10-09) — the company names ONE accessory
// number at the top of the page; the table says who has to sell what to get there, weighted by each
// store's OWN accessories-per-box history against the boxes it is actually selling.
//
// Everything here is computed by `GET /commcalc/accessory-target-plan/{period}` and NOTHING is derived
// in the browser: the goal, the per-store capacity, the proportional split and the rounding that makes
// the column foot all live in `backend/app/modules/commcalc/accessory_target_plan.py`, so this page and
// the assign button can never disagree about what a store's target is going to be.
//
// The "Assign" button writes into the SAME target row the Accessory Sales Targets tracker reads
// (`commcalc.targets.accessories_monthly`, mig 006) through the same permission + store-span gate as
// the single-store save — there is no second home for a store's accessory target.

type Row = {
  store_code: string; address: string; market: string | null; is_active?: boolean
  m2_acc: number; m2_boxes: number; m1_acc: number; m1_boxes: number
  mtd_acc: number; mtd_boxes: number; projected_acc: number; projected_boxes: number
  current_target: number; has_target_row?: boolean
  hist_acc: number; hist_boxes: number
  acc_per_box: number | null; acc_per_box_basis: string
  expected_boxes: number; expected_boxes_basis: string
  capacity: number | null
  selected: boolean
  suggested_target: number | null; extension: number | null
  required_acc_per_box: number | null; gap_vs_projection: number | null; share_pct: number | null
}
type Opt = { value: string; label?: string }
type Plan = {
  period: string; today: string
  history_periods: { m1: string; m2: string }
  goal: { goal: number | null; mode: string; basis: string; basis_amount: number | null; value: number | null; reason: string | null }
  goal_basis_amounts: Record<string, number>
  company_acc_per_box: number | null
  allocation: { reserved: number; to_split: number | null; weight_basis: string | null; unweighted: string[]; reason: string | null; shortfall: number | null }
  rows: Row[]
  totals: Record<string, number>
  goal_modes: Opt[]; goal_bases: Opt[]
  filters: { stores: Opt[]; markets: string[] }
  scope: { restricted: boolean; can_edit_targets: boolean; stores_in_scope: number }
}

// Why a store is weighted the way it is. Shown on the row, so a suggestion resting on the company's
// rate can never be mistaken for a measurement of that store.
const RATE_WHY: Record<string, string> = {
  own_history: "This store's own accessories-per-box over the last two months.",
  company_rate_zero_attach: 'This store sold boxes and attached NO accessories in either month, so there is no rate of its own to use. It is weighted at the company rate — the suggestion is a goal, not a measurement of this store.',
  company_rate_no_history: 'No boxes sold in either history month (new, reopened, or no feed), so it is weighted at the company rate.',
  no_basis: 'Nothing measurable anywhere in the window — no rate can be computed, so no target is suggested.',
}
const BOX_WHY: Record<string, string> = {
  projection: 'Projected month-end boxes, the same projection Executive MTD shows.',
  history_average: 'No projection yet this month, so the average of the two history months stands in.',
  none: 'No boxes projected and none in history — there is nothing to multiply the rate by.',
}
const GOAL_WHY: Record<string, string> = {
  no_goal_entered: 'Enter a company goal above and the suggested targets appear. Nothing is planned until you do.',
  no_basis: 'A percentage needs something to be a percentage OF, and the basis you picked measures $0 for this window. Pick another basis, or enter a fixed total.',
  unknown_mode: 'That goal type is not one the report offers.',
  negative_clamped_to_zero: 'A negative total was entered; it has been read as $0.',
  decrease_over_100_clamped_to_zero: 'A decrease of more than 100% would plan negative sales; the goal has been read as $0.',
}
const ALLOC_WHY: Record<string, string> = {
  goal_already_committed: 'The stores you did NOT select already carry targets that add up to more than the whole company goal, so there is nothing left to split. Either raise the goal or include more stores in the selection.',
  no_stores_selected: 'None of the stores you picked are in this window.',
  no_goal: '',
}

// Click-a-header sorting (owner directive 2026-08-10) reads each cell through ONE accessor, so the
// column a manager clicks sorts on the VALUE and not on the formatted string beside it. `null` stays
// null rather than becoming 0: `compareValues` sinks an empty cell in both directions, which is the
// honesty rule this report keeps everywhere else — "could not be computed" is not "zero".
function cell(r: Row, field: string): unknown {
  switch (field) {
    case 'store': return r.address || r.store_code
    case 'm2_acc': return r.m2_acc
    case 'm1_acc': return r.m1_acc
    case 'mtd_acc': return r.mtd_acc
    case 'projected_acc': return r.projected_acc
    case 'acc_per_box': return r.acc_per_box
    case 'expected_boxes': return r.expected_boxes
    case 'capacity': return r.capacity
    case 'current_target': return r.current_target
    case 'suggested_target': return r.suggested_target
    case 'extension': return r.extension
    case 'required_acc_per_box': return r.required_acc_per_box
    case 'share_pct': return r.share_pct
    default: return undefined
  }
}

const money = (v: number | null | undefined) => (v === null || v === undefined ? '—' : fmt(Number(v)))
const rate = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `$${Number(v).toFixed(2)}`)

export default function AccessoryTargetPlanPage() {
  const { period } = usePeriod()
  const [plan, setPlan] = useState<Plan | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  // ── The user-defined company goal, at the top of the page (the owner's ask, verbatim) ───────────
  const [mode, setMode] = useState('pct_increase')
  const [basis, setBasis] = useState('last_month_actual')
  const [value, setValue] = useState('')
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])

  const [assigning, setAssigning] = useState(false)
  const [assigned, setAssigned] = useState<{ written: number; reason: string | null } | null>(null)

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    qs.set('org_id', ORG_ID); qs.set('today', localToday())
    qs.set('mode', mode); qs.set('basis', basis)
    if (value.trim() !== '') qs.set('value', value.trim())
    selStores.forEach((s) => qs.append('stores', s))
    selMarkets.forEach((s) => qs.append('markets', s))
    api(`/api/v1/commcalc/accessory-target-plan/${encodeURIComponent(period)}?${qs.toString()}`)
      .then((d: Plan) => { setPlan(d); setAssigned(null) })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [period, mode, basis, value, selStores, selMarkets])
  useEffect(() => { load() }, [load])

  const rows = useMemo(() => plan?.rows || [], [plan])
  // `initial: null` keeps the server's own capacity ranking as the default order.
  const { sorted, sort, toggle } = useTableSort<Row>(rows, cell)
  const picked = useMemo(() => rows.filter((r) => r.selected), [rows])
  const writable = useMemo(() => picked.filter((r) => r.suggested_target !== null), [picked])
  const goal = plan?.goal
  const alloc = plan?.allocation

  async function assign() {
    if (!plan || writable.length === 0) return
    const names = writable.length === rows.length ? 'all stores' : `${writable.length} store(s)`
    if (!window.confirm(
      `Set the accessory target for ${names} for ${plan.period}?\n\n` +
      `Total being assigned: ${fmt(plan.totals.assigned || 0)}\n` +
      `This replaces the accessory target on those stores. Nothing else on their target row changes.`
    )) return
    setAssigning(true)
    try {
      const d: { written?: number; reason?: string | null } = await api(`/api/v1/commcalc/accessory-target-plan/${encodeURIComponent(plan.period)}/assign?org_id=${ORG_ID}&today=${localToday()}`, {
        method: 'POST',
        body: JSON.stringify({
          mode, basis, value: value.trim() === '' ? null : value.trim(),
          stores: selStores, markets: selMarkets,
          // Belt and braces: the server recomputes the plan, and may only write the codes shown here.
          confirm_store_codes: writable.map((r) => r.store_code),
        }),
      })
      setAssigned({ written: Number(d?.written || 0), reason: d?.reason ?? null })
      load()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setAssigning(false)
    }
  }

  function buildPayload(): ExportPayload {
    const m1 = plan?.history_periods?.m1 || 'last month'
    const m2 = plan?.history_periods?.m2 || 'two months ago'
    return {
      title: 'Accessory Target Allocation', subtitle: `${period} — goal ${money(goal?.goal ?? null)}`,
      filename: `accessory-target-plan_${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (r: Row) => r.address || r.store_code },
        { header: 'Market', get: (r: Row) => r.market || '' },
        { header: `${m2} acc $`, get: (r: Row) => r.m2_acc, money: true },
        { header: `${m2} boxes`, get: (r: Row) => r.m2_boxes },
        { header: `${m1} acc $`, get: (r: Row) => r.m1_acc, money: true },
        { header: `${m1} boxes`, get: (r: Row) => r.m1_boxes },
        { header: 'MTD acc $', get: (r: Row) => r.mtd_acc, money: true },
        { header: 'Projected acc $', get: (r: Row) => r.projected_acc, money: true },
        { header: 'Acc $ per box (history)', get: (r: Row) => r.acc_per_box ?? '' },
        { header: 'Rate basis', get: (r: Row) => r.acc_per_box_basis },
        { header: 'Expected boxes', get: (r: Row) => r.expected_boxes },
        { header: 'Capacity $', get: (r: Row) => r.capacity ?? '', money: true },
        { header: 'Current target $', get: (r: Row) => r.current_target, money: true },
        { header: 'Proportionate target $', get: (r: Row) => r.suggested_target ?? '', money: true },
        { header: 'Extension $', get: (r: Row) => r.extension ?? '', money: true },
        { header: 'Required acc $ per box', get: (r: Row) => r.required_acc_per_box ?? '' },
        { header: 'vs projection $', get: (r: Row) => r.gap_vs_projection ?? '', money: true },
        { header: 'Share of goal %', get: (r: Row) => r.share_pct ?? '' },
        { header: 'In selection', get: (r: Row) => (r.selected ? 'yes' : 'no') },
      ] }],
    }
  }

  const isPct = mode !== 'fixed'
  const m1Label = plan?.history_periods?.m1 || 'Last month'
  const m2Label = plan?.history_periods?.m2 || '2 months ago'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/targets/accessories" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Accessory Sales Targets</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>🎯 Accessory Target Allocation</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Set <b>one company accessory goal</b> and this works out what each store has to carry — weighted
            by its own <b>accessories per box</b> over the last two months against the <b>boxes it is actually
            selling</b>. Assign it to every store or just the ones you pick.
          </p>
        </div>
        {rows.length > 0 && <div style={{ display: 'flex', gap: 8 }}><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></div>}
      </div>

      {/* ── THE GOAL, USER-DEFINED, ON TOP OF THE PAGE ─────────────────────────────────────────── */}
      <div className="card" style={{ padding: '14px 16px', marginBottom: 14 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 10 }}>
          Company accessory goal for {period}
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <Field label="Goal type">
            <select value={mode} onChange={(e) => setMode(e.target.value)} style={SEL}>
              {(plan?.goal_modes || [{ value: 'pct_increase', label: '% increase over the basis' }]).map((o) =>
                <option key={o.value} value={o.value}>{o.label || o.value}</option>)}
            </select>
          </Field>
          <Field label={isPct ? 'Percent' : 'Company total ($)'}>
            <input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal"
              placeholder={isPct ? 'e.g. 10' : 'e.g. 250000'} style={{ ...SEL, width: 130, textAlign: 'right' }} />
          </Field>
          {isPct && (
            <Field label="Percent of what">
              <select value={basis} onChange={(e) => setBasis(e.target.value)} style={{ ...SEL, minWidth: 250 }}>
                {(plan?.goal_bases || []).map((o) => {
                  const amt = plan?.goal_basis_amounts?.[o.value]
                  return <option key={o.value} value={o.value}>{(o.label || o.value) + (amt !== undefined ? ` — ${fmt(amt)}` : '')}</option>
                })}
              </select>
            </Field>
          )}
          <Field label="Assign to">
            {(plan?.filters.stores?.length || 0) > 0
              ? <MultiSelect allLabel="All stores" width={200} value={selStores} options={plan!.filters.stores} onChange={setSelStores} searchable />
              : <span style={{ fontSize: 12, color: 'var(--text3)' }}>no stores in scope</span>}
          </Field>
          {(plan?.filters.markets?.length || 0) > 0 && (
            <Field label="Market">
              <MultiSelect allLabel="All markets" width={150} value={selMarkets}
                options={(plan!.filters.markets).map((m) => ({ value: m }))} onChange={setSelMarkets} />
            </Field>
          )}
          <div style={{ flex: 1 }} />
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', fontWeight: 600 }}>Company goal</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: goal?.goal === null ? 'var(--text3)' : 'var(--accent)' }}>
              {money(goal?.goal ?? null)}
            </div>
          </div>
        </div>
        {goal?.reason && GOAL_WHY[goal.reason] !== undefined && (
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 10, padding: '8px 11px', background: 'var(--surface2)', borderRadius: 6 }}>
            ℹ️ {GOAL_WHY[goal.reason] || goal.reason}
          </div>
        )}
        {alloc?.reason && ALLOC_WHY[alloc.reason] && (
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 10, padding: '8px 11px', background: '#fff7ed', border: '1px solid #fdba74', borderRadius: 6 }}>
            ⚠️ {ALLOC_WHY[alloc.reason]}
            {alloc.reason === 'goal_already_committed' && alloc.shortfall ? ` They exceed it by ${fmt(alloc.shortfall)}.` : ''}
          </div>
        )}
      </div>

      {err && <div className="card" style={{ padding: '10px 14px', marginBottom: 12, borderLeft: '3px solid #dc2626', fontSize: 13 }}>{err}</div>}
      {assigned && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, borderLeft: '3px solid #16a34a', fontSize: 13 }}>
          {assigned.written > 0
            ? <>✅ Accessory target set on <b>{assigned.written}</b> store{assigned.written === 1 ? '' : 's'} for {plan?.period}. They now show on the <a href="/commcalc/targets/accessories">Accessory Sales Targets</a> tracker.</>
            : <>Nothing was written{assigned.reason ? ` — ${assigned.reason.replace(/_/g, ' ')}` : ''}.</>}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          No stores in scope for {period}.
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(165px, 1fr))', gap: 14, marginBottom: 16 }}>
            <Stat label={`${m1Label} actual`} value={fmt(plan!.totals.m1_acc)} />
            <Stat label="Projected this month" value={fmt(plan!.totals.projected_acc)} />
            <Stat label="Targets set now" value={fmt(plan!.totals.current_target)} />
            <Stat label="Being assigned" value={money(goal?.goal === null ? null : plan!.totals.assigned)} color="var(--accent)"
              sub={alloc?.reserved ? `+ ${fmt(alloc.reserved)} reserved on unselected stores` : undefined} />
            <Stat label="Total extension" value={goal?.goal === null ? '—' : fmt(plan!.totals.extension)}
              color={plan!.totals.extension >= 0 ? '#16a34a' : '#d97706'}
              sub={`${plan!.totals.stores_selected} of ${plan!.totals.stores} stores selected`} />
          </div>

          {plan!.scope.can_edit_targets && (
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" disabled={assigning || writable.length === 0} onClick={assign}>
                {assigning ? 'Assigning…' : `Assign ${writable.length > 0 ? fmt(plan!.totals.assigned) + ' to ' + writable.length + ' store' + (writable.length === 1 ? '' : 's') : 'targets'}`}
              </button>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                Writes the accessory target for {period} on the selected stores. Nothing else on their target row changes, and a store with no suggestion is left alone.
              </span>
            </div>
          )}

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1280 }}>
              <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <SortableTh field="store" sort={sort} onSort={toggle} style={TH_L}>Store</SortableTh>
                <SortableTh field="m2_acc" sort={sort} onSort={toggle} style={TH_R} title={`Accessory sales (accessory $ + device set-up fee) in ${m2Label}, with the boxes sold.`}>{m2Label}</SortableTh>
                <SortableTh field="m1_acc" sort={sort} onSort={toggle} style={TH_R} title={`Accessory sales in ${m1Label}, with the boxes sold.`}>{m1Label}</SortableTh>
                <SortableTh field="mtd_acc" sort={sort} onSort={toggle} style={TH_R} title="Accessory sales so far this month.">MTD</SortableTh>
                <SortableTh field="projected_acc" sort={sort} onSort={toggle} style={TH_R} title="Projected month-end, the same projection Executive MTD and the Accessory Sales Targets tracker show.">Projected</SortableTh>
                <SortableTh field="acc_per_box" sort={sort} onSort={toggle} style={TH_R} title="Accessory $ per box over the two history months — the weight this store's share is computed from.">$ / box</SortableTh>
                <SortableTh field="expected_boxes" sort={sort} onSort={toggle} style={TH_R} title="The boxes the share is measured against: the projected month-end boxes, or the history average when there is no projection yet.">Boxes used</SortableTh>
                <SortableTh field="capacity" sort={sort} onSort={toggle} style={TH_R} title="$ per box x boxes used. What this store is demonstrated to be able to carry; shares are proportional to it.">Capacity</SortableTh>
                <SortableTh field="current_target" sort={sort} onSort={toggle} style={TH_R} title="The accessory target in force for this month.">Current target</SortableTh>
                <SortableTh field="suggested_target" sort={sort} onSort={toggle} style={TH_R} title="This store's proportional share of the company goal.">Proportionate target</SortableTh>
                <SortableTh field="extension" sort={sort} onSort={toggle} style={TH_R} title="Proportionate target minus the current target — what the store is being asked for on top of what it already carries.">Extension</SortableTh>
                <SortableTh field="required_acc_per_box" sort={sort} onSort={toggle} style={TH_R} title="The accessory $ per box the suggested target implies. Compare it with the $ / box column: if it is far higher, the ask is attachment, not traffic.">Required $ / box</SortableTh>
                <SortableTh field="share_pct" sort={sort} onSort={toggle} style={TH_R} title="Share of the amount being split.">Share</SortableTh>
              </tr></thead>
              <tbody>
                {sorted.map((r) => {
                  const dim = !r.selected
                  const basisOdd = r.acc_per_box_basis !== 'own_history'
                  return (
                    <tr key={r.store_code} style={{ borderTop: '1px solid var(--border)', opacity: dim ? 0.5 : 1 }}>
                      <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>
                        {r.address || r.store_code}
                        {r.is_active === false && <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}> (inactive)</span>}
                        {dim && <span style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}> · not in selection</span>}
                      </td>
                      <Td>{fmt(r.m2_acc)}<Sub>{r.m2_boxes} boxes</Sub></Td>
                      <Td>{fmt(r.m1_acc)}<Sub>{r.m1_boxes} boxes</Sub></Td>
                      <Td>{fmt(r.mtd_acc)}<Sub>{r.mtd_boxes} boxes</Sub></Td>
                      <Td>{fmt(r.projected_acc)}<Sub>{r.projected_boxes} boxes</Sub></Td>
                      <Td title={RATE_WHY[r.acc_per_box_basis]}>
                        <span style={{ color: basisOdd ? '#d97706' : undefined, fontWeight: basisOdd ? 600 : 400 }}>{rate(r.acc_per_box)}</span>
                        {basisOdd && <Sub>{r.acc_per_box_basis === 'company_rate_zero_attach' ? 'no attachment — company rate' : r.acc_per_box_basis === 'company_rate_no_history' ? 'no history — company rate' : 'no basis'}</Sub>}
                      </Td>
                      <Td title={BOX_WHY[r.expected_boxes_basis]}>{r.expected_boxes}
                        {r.expected_boxes_basis !== 'projection' && <Sub>{r.expected_boxes_basis === 'history_average' ? 'history avg' : 'none'}</Sub>}
                      </Td>
                      <Td>{money(r.capacity)}</Td>
                      <Td>{fmt(r.current_target)}</Td>
                      <Td bold color="var(--accent)">{money(r.suggested_target)}</Td>
                      <Td bold color={r.extension === null ? undefined : r.extension >= 0 ? '#16a34a' : '#d97706'}>
                        {r.extension === null ? '—' : (r.extension >= 0 ? '+' : '') + fmt(r.extension)}
                      </Td>
                      <Td>{rate(r.required_acc_per_box)}</Td>
                      <Td>{r.share_pct === null ? '—' : `${r.share_pct.toFixed(1)}%`}</Td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot><tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2)', fontWeight: 700, fontSize: 13 }}>
                <td style={{ padding: '9px 12px' }}>Total</td>
                <Td>{fmt(plan!.totals.m2_acc)}</Td>
                <Td>{fmt(plan!.totals.m1_acc)}</Td>
                <Td>{fmt(plan!.totals.mtd_acc)}</Td>
                <Td>{fmt(plan!.totals.projected_acc)}</Td>
                <Td>—</Td><Td>—</Td><Td>—</Td>
                <Td>{fmt(plan!.totals.current_target)}</Td>
                <Td color="var(--accent)">{money(goal?.goal === null ? null : plan!.totals.assigned)}</Td>
                <Td>{goal?.goal === null ? '—' : fmt(plan!.totals.extension)}</Td>
                <Td>—</Td>
                <Td>{alloc?.to_split ? '100%' : '—'}</Td>
              </tr></tfoot>
            </table>
          </div>

          {(alloc?.unweighted?.length || 0) > 0 && (
            <div className="card" style={{ padding: '10px 14px', marginTop: 12, borderLeft: '3px solid #d97706', fontSize: 12.5, color: 'var(--text2)' }}>
              ⚠️ <b>{alloc!.unweighted.length} store{alloc!.unweighted.length === 1 ? '' : 's'} could not be given a suggestion</b> — {alloc!.unweighted.join(', ')}.
              There is no accessories-per-box rate and no box projection to work from, so no number can be
              derived for them. Their current target is left exactly as it is; nothing is written as $0.
            </div>
          )}

          <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 12, padding: '10px 12px', background: 'var(--surface2)', borderRadius: 6, maxWidth: 1000 }}>
            ℹ️ <b>How a store&apos;s share is worked out.</b> Its accessories-per-box rate over{' '}
            {m2Label} and {m1Label} (accessory $ + device set-up fee ÷ boxes sold — the same accessory
            basis the <a href="/commcalc/targets/accessories">Accessory Sales Targets</a> tracker uses),
            multiplied by the boxes it is projected to sell this month. That product is its{' '}
            <b>capacity</b>, and the company goal is split in proportion to it, rounded so the column
            adds up to the goal exactly. The rate is history and the box count is this month on purpose:
            attachment is how a store sells, traffic is what it has this month. A store that sold boxes
            and attached nothing is weighted at the <b>company</b> rate
            {plan!.company_acc_per_box !== null ? ` (${rate(plan!.company_acc_per_box)} per box)` : ''} and
            flagged, because weighting it at zero would hand it a $0 target and call that planning.
            Set-up-fee recognition is per tenant in{' '}
            <ScreenLink to="sales_report_settings">Sales Report → Accessory settings</ScreenLink>. Nothing
            on this page affects anyone&apos;s pay — an accessory target is a sales goal, never a payout input.
          </p>
        </>
      )}
    </div>
  )
}

// The two header stylings the old private <Th> carried, now handed to the SHARED SortableTh so the
// column keeps its look and gains the ▲/▼/↕ affordance and aria-sort for free.
const TH_L: React.CSSProperties = { textAlign: 'left', padding: '9px 12px', whiteSpace: 'nowrap' }
const TH_R: React.CSSProperties = { textAlign: 'right', padding: '9px 12px', whiteSpace: 'nowrap' }

const SEL: React.CSSProperties = { padding: '7px 9px', fontSize: 13, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text1)' }

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div>
    <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: 4 }}>{label}</div>
    {children}
  </div>
}

function Td({ children, bold, color, title }: { children?: React.ReactNode; bold?: boolean; color?: string; title?: string }) {
  return <td title={title} style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, fontWeight: bold ? 600 : 400, color }}>{children}</td>
}

function Sub({ children }: { children?: React.ReactNode }) {
  return <div style={{ fontSize: 10.5, color: 'var(--text3)', fontWeight: 400 }}>{children}</div>
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
}
