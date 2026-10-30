'use client'
import { Fragment, useState, useEffect, useCallback, useMemo } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'
import { SortableTh, useTableSort } from '@/components/SortableTh'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillPanel,
} from '@/components/DrillDownPanel'
import { agreesToTheCent } from '@/lib/drill-narrow'

// ACCESSORY ORDER PLAN (index §COMMCALC-49, owner ask 2026-10-29) — the device models the stores
// actually sold, ranked best-seller first; a human says which are CURRENT and which are END OF LIFE;
// the accessory order follows from the rulings.
//
// THIS PAGE RENDERS AND NEVER COMPUTES. The ranking, the per-model average, the projection and the
// colour split all live in `backend/app/modules/commcalc/accessory_order_plan.py`, and the
// velocity/horizon arithmetic is the one home `/payables/forecast` reads too
// (`commcalc/demand_projection.py`). In particular this page never fills a blank with a 0: a model
// nobody has ruled on shows a dash and a prompt, because a 0 there would read as a decision to stop
// ordering for it.
//
// WHERE EACH FIGURE CAME FROM (§CORE-17.3z). The four counted figures open to the model rows behind
// them, declared in `commcalc/accessory_order_drilldown.py` and folded once by
// `core/report_drilldown`. The rates — the declared share and the per-month run rate — are refused a
// panel (§CORE-17.3h), and so are the accessory quantities, which are the device total times a
// declared rate and therefore have no rows of their own (§CORE-17.3k).
//
// EVERY FILTER IS A QUERY PARAMETER, so the server folds figure and panel together on each change
// and `lib/drill-narrow` is used only for the agree-to-the-cent gate, never to narrow a panel.

type MonthUnits = { period: string; units: number }
type Variant = { model: string; units_total: number; units_by_month: Record<string, number> }
type ModelRow = {
  model: string; rank: number
  units_by_month: MonthUnits[]
  units_total: number; months_observed: number; avg_monthly: number | null
  share: number; lifecycle: string; lifecycle_label: string
  history_basis: string; projected_units: number | null; projection_basis: string
  variants: Variant[]; variant_count: number
}
type Option = { key: string; label: string; image_url?: string | null }
type OptionQty = { key: string; label: string; share: number; qty: number | null }
type GridColumn = {
  category: string; category_label: string
  dimension: string; dimension_label: string
  per_device: number | null; options: Option[]
}
type Cell = {
  qty: number | null; per_device: number | null; reason: string | null
  options: OptionQty[]; colours?: OptionQty[]
}
type ModelOrder = {
  model: string; rank: number; units_total: number
  lifecycle: string; lifecycle_label: string
  projected_units: number | null; projection_basis: string
  variants: Variant[]; cells: Record<string, Cell>
}
type Dimension = { key: string; label: string; default: string; options: Option[] }
type ConsolidatedLine = {
  spec: Record<string, string>; label: string; qty: number
  models: { model: string; qty: number; spec_basis: string }[]; spec_basis: string
}
type Consolidated = {
  category: string; category_label: string; per_device: number | null
  dimensions: Dimension[]; lines: ConsolidatedLine[]; qty: number
  awaiting: { model: string; reason: string }[]; reason: string | null
}
type Category = {
  key: string; label: string; grain: string; per_device: number | null
  colour_split: boolean; dimensions: Dimension[]
}
type Colour = { key: string; label: string; share: number; qty: number | null }
type AccessoryRow = {
  key: string; label: string; per_device: number; qty: number | null
  colour_split: boolean; colours?: Colour[]
}
type Plan = {
  period: string; months: string[]; lookback_months: number; horizon_months: number
  models: ModelRow[]
  models_awaiting_declaration: string[]; units_awaiting_declaration: number
  units_in_window: number; units_declared: number; declared_share: number
  projected_devices: number | null; projected_devices_per_month: number | null
  accessories: AccessoryRow[]
  grid_columns: GridColumn[]
  model_orders: ModelOrder[]
  consolidated: Consolidated[]
  catalog: Category[]; catalog_basis: string
  option_mix: Record<string, OptionQty[]>; option_mix_bases: Record<string, string>
  colour_tokens: string[]; colour_tokens_basis: string
  accessory_device_classes: string[]
  case_mix: Colour[]; case_mix_basis: string
  attach_plan: { key: string; label: string; per_device: number; colour_split: boolean }[]
  lifecycle_states: string[]; lifecycle_labels: Record<string, string>
  reason: string | null
  store_options: string[]; markets: string[]
  params: Record<string, unknown>
  source_meta: { device_lines: number; models: number; raw_models?: number
                 skipped_by_class?: Record<string, number> }
  drilldown?: Record<string, DrillPanel>
}

// Why a model carries no projected figure, in the words a planner needs rather than the field name.
const WHY: Record<string, string> = {
  awaiting_declaration: 'Nobody has said yet whether this model is current or end of life. Until somebody does, it is deliberately left out of the order — projecting it would be the report making that call for you.',
  end_of_life: 'Ruled end of life, so it is deliberately not being ordered for. Its sales history is still shown, because that demand was real.',
  measured: 'Its own monthly average over the months it was selling, carried across the planning months.',
  no_window: 'No month in the look-back could be measured for this model.',
}
const PLAN_WHY: Record<string, string> = {
  awaiting_declaration: 'No model has been ruled on yet, so there is nothing to order. Mark the best sellers below as Current — the order appears as soon as you do.',
  no_sales_in_window: 'No device sales at all in this window, for these stores. Widen the look-back or the store selection.',
}
const MIX_WHY: Record<string, string> = {
  house_default: 'the house mix (blacks and blues 70%, pinks and reds 30%)',
  declared: 'your declared mix',
  declared_normalised: 'your declared mix, rescaled — the shares you entered did not add up to 100%',
}
// The figures that open to rows, in the order the tiles print them.
const FIGURES: [string, string][] = [
  ['units_in_window', 'devices sold in the look-back'],
  ['units_declared', 'units on ruled models'],
  ['units_awaiting_declaration', 'units awaiting a ruling'],
  ['projected_devices', 'devices projected'],
]

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const num = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? '—' : digits ? v.toFixed(digits) : String(Math.round(v))
const share = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`

export default function AccessoryOrderPlanPage() {
  const { period } = usePeriod()
  const [months, setMonths] = useState(3)        // the owner's own default look-back
  const [horizon, setHorizon] = useState(2)      // and his own planning window
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [data, setData] = useState<Plan | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  // The rulings the user has ticked but not yet saved, so the table can show intent before the save.
  const [pending, setPending] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [reload, setReload] = useState(0)
  // Which phone's row is open, showing the colours that were actually sold under it (owner
  // 2026-10-30: *"with a drill down option to see what was sold"*). The variants come down on the
  // row itself, so opening one costs no query.
  const [openRow, setOpenRow] = useState('')
  // The declarations being edited. `null` means "not being edited", which is different from an
  // empty edit — the panel only ever writes what the user actually touched.
  const [mixDraft, setMixDraft] = useState<Colour[] | null>(null)
  const [styleDraft, setStyleDraft] = useState<Record<string, OptionQty[]> | null>(null)
  const [newOpt, setNewOpt] = useState<Record<string, { label: string; image: string }>>({})
  const [specDraft, setSpecDraft] = useState<Record<string, string>>({})
  const [showSetup, setShowSetup] = useState(false)

  // THE FETCH LIVES IN THE EFFECT AND SETS STATE ONLY IN ITS CALLBACKS. Flipping `loading` from
  // inside the effect body is a cascading render (`react-hooks/set-state-in-effect`), so the spinner
  // is raised by the control the user actually touched — `reread()` below — and lowered here.
  useEffect(() => {
    let alive = true
    const qs = new URLSearchParams()
    qs.set('org_id', ORG_ID); qs.set('period', period)
    qs.set('months', String(months)); qs.set('horizon', String(horizon))
    if (selStores.length) qs.set('stores', selStores.join(','))
    if (selMarkets.length) qs.set('markets', selMarkets.join(','))
    api(`/api/v1/commcalc/accessory-order-plan?${qs.toString()}`)
      .then((d: Plan) => { if (!alive) return; setData(d); setPending({}); setErr('') })
      .catch((e: unknown) => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [period, months, horizon, selStores, selMarkets, reload])
  // Raise the spinner and re-run the effect. Called from a handler, never from an effect.
  const reread = useCallback(() => { setLoading(true); setReload(n => n + 1) }, [])

  const rows = useMemo(() => data?.models || [], [data])
  const cell = useCallback((r: ModelRow, field: string): unknown => {
    if (field.startsWith('m:')) {
      const p = field.slice(2)
      return r.units_by_month.find(m => m.period === p)?.units ?? null
    }
    switch (field) {
      case 'model': return r.model
      case 'units_total': return r.units_total
      case 'avg_monthly': return r.avg_monthly
      case 'share': return r.share
      case 'lifecycle': return r.lifecycle
      case 'projected_units': return r.projected_units
      default: return undefined
    }
  }, [])
  const { sorted, sort, toggle } = useTableSort<ModelRow>(rows, cell)

  const accRows = useMemo(() => data?.accessories || [], [data])
  const accCell = useCallback((r: AccessoryRow, field: string): unknown => {
    switch (field) {
      case 'label': return r.label
      case 'per_device': return r.per_device
      case 'qty': return r.qty
      default: return undefined
    }
  }, [])
  const acc = useTableSort<AccessoryRow>(accRows, accCell)

  // A panel is offered only where it agrees with the tile to the cent — one home for that rule, so a
  // panel can never explain a number the page is not showing.
  const drill = useDrillTile(data?.drilldown || {},
    (k, p) => agreesToTheCent(p, (data as unknown as Record<string, number>)?.[k]))

  const stateOf = (r: ModelRow) => pending[r.model] ?? (r.lifecycle === 'undeclared' ? '' : r.lifecycle)
  const dirty = useMemo(() => Object.keys(pending).length > 0, [pending])

  async function saveRulings() {
    if (!dirty) return
    setSaving(true); setErr('')
    try {
      const rulings = Object.entries(pending).filter(([, v]) => v !== '')
        .map(([model, lifecycle]) => ({ model, lifecycle }))
      const clear = Object.entries(pending).filter(([, v]) => v === '').map(([model]) => model)
      await api(`/api/v1/commcalc/accessory-order-plan/lifecycle?org_id=${ORG_ID}`,
        { method: 'POST', body: JSON.stringify({ rulings, clear }) })
      reread()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  // ONE writer for every declaration on this screen. The screen never computes a quantity and it
  // never stores a derived one either: it posts what the user declared and re-reads the plan, so
  // what is on screen after a save is what the server made of it.
  const saveConfig = useCallback(async (patch: Record<string, unknown>) => {
    setSaving(true); setErr('')
    try {
      await api(`/api/v1/commcalc/accessory-order-plan/config?org_id=${ORG_ID}`,
        { method: 'PUT', body: JSON.stringify(patch) })
      setMixDraft(null); setStyleDraft(null); setNewOpt({})
      reread()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setSaving(false) }
  }, [reread])

  const saveSpecs = useCallback(async () => {
    const specs = Object.entries(specDraft).filter(([, opt]) => opt)
      .map(([k, option]) => { const [category, dimension, ...m] = k.split('|'); return { model: m.join('|'), category, dimension, option } })
    if (!specs.length) return
    setSaving(true); setErr('')
    try {
      await api(`/api/v1/commcalc/accessory-order-plan/model-spec?org_id=${ORG_ID}`,
        { method: 'PUT', body: JSON.stringify({ specs }) })
      setSpecDraft({}); reread()
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setSaving(false) }
  }, [specDraft, reread])

  // The catalog with one option ADDED to a dimension — the *"option to add more with pictures"*
  // half of the ask. Sent as the whole catalog because that is the shape the declaration is stored
  // in; the server resolves it and hands back what it kept.
  const addOption = useCallback((catKey: string, dimKey: string) => {
    const draft = newOpt[`${catKey}|${dimKey}`]
    if (!draft?.label.trim() || !data) return
    const key = draft.label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
    if (!key) return
    const next = data.catalog.map(c => c.key !== catKey ? c : ({
      ...c,
      dimensions: c.dimensions.map(d => d.key !== dimKey ? d : ({
        ...d,
        options: [...d.options, { key, label: draft.label.trim(),
                                 image_url: draft.image.trim() || null }],
      })),
    }))
    void saveConfig({ catalog: next })
  }, [newOpt, data, saveConfig])

  function buildPayload(): ExportPayload {
    const ms = data?.months || []
    return {
      title: 'Accessory Order Plan',
      subtitle: `${period} — ${data?.lookback_months ?? months} months back, ordering for the next ${data?.horizon_months ?? horizon}`,
      filename: `accessory-order-plan_${period.replace(/\s+/g, '-')}`,
      sheets: [
        { name: 'By model', rows, columns: [
          { header: 'Rank', get: (r: ModelRow) => r.rank },
          { header: 'Model', get: (r: ModelRow) => r.model },
          ...ms.map(p => ({ header: p, get: (r: ModelRow) => r.units_by_month.find(m => m.period === p)?.units ?? 0 })),
          { header: 'Units total', get: (r: ModelRow) => r.units_total },
          { header: 'Months selling', get: (r: ModelRow) => r.months_observed },
          { header: 'Avg per month', get: (r: ModelRow) => r.avg_monthly ?? '' },
          { header: 'Share of demand', get: (r: ModelRow) => r.share },
          { header: 'Ruling', get: (r: ModelRow) => r.lifecycle },
          { header: 'Projected units', get: (r: ModelRow) => r.projected_units ?? '' },
          { header: 'Why', get: (r: ModelRow) => r.projection_basis },
        ] },
        // THE ORDER SHEET IS THE GRID, one row per phone and a column per style — the shape the
        // owner asked for, so the export and the screen are the same sheet.
        { name: 'Order by phone', rows: data?.model_orders || [], columns: [
          { header: 'Rank', get: (r: ModelOrder) => r.rank },
          { header: 'Phone', get: (r: ModelOrder) => r.model },
          { header: 'Colours sold', get: (r: ModelOrder) => r.variants.map(v => v.model).join(' | ') },
          { header: 'Sold in look-back', get: (r: ModelOrder) => r.units_total },
          { header: 'Ruling', get: (r: ModelOrder) => r.lifecycle },
          { header: 'Projected', get: (r: ModelOrder) => r.projected_units ?? '' },
          ...(data?.grid_columns || []).flatMap(c => [
            ...c.options.map(o => ({
              header: `${c.category_label}: ${o.label}`,
              get: (r: ModelOrder) => r.cells[c.category]?.options.find(x => x.key === o.key)?.qty ?? '',
            })),
            { header: `${c.category_label} total`,
              get: (r: ModelOrder) => r.cells[c.category]?.qty ?? '' },
          ]),
          ...(data?.case_mix || []).map(g => ({
            header: `Case ${g.label}`,
            get: (r: ModelOrder) => r.cells.case?.colours?.find(c => c.key === g.key)?.qty ?? '',
          })),
        ] },
        { name: 'Consolidated', rows: (data?.consolidated || []).flatMap(c =>
            c.lines.map(l => ({ category: c.category_label, label: l.label, qty: l.qty,
                                models: l.models.map(m => `${m.model} (${m.qty})`).join(' | ') }))),
          columns: [
            { header: 'Accessory', get: (r: { category: string }) => r.category },
            { header: 'Specification', get: (r: { label: string }) => r.label },
            { header: 'Quantity', get: (r: { qty: number }) => r.qty },
            { header: 'Phones it covers', get: (r: { models: string }) => r.models },
          ] },
        { name: 'Totals', rows: accRows, columns: [
          { header: 'Accessory', get: (r: AccessoryRow) => r.label },
          { header: 'Per device', get: (r: AccessoryRow) => r.per_device },
          { header: 'Quantity', get: (r: AccessoryRow) => r.qty ?? '' },
          { header: 'Colour split', get: (r: AccessoryRow) =>
            (r.colours || []).map(c => `${c.label}: ${c.qty ?? '—'}`).join(' · ') },
        ] },
      ],
    }
  }

  const tiles: [string, string, number | null][] = [
    ['units_in_window', 'Devices sold in the look-back', data?.units_in_window ?? null],
    ['units_declared', 'Units on models you have ruled on', data?.units_declared ?? null],
    ['units_awaiting_declaration', 'Units still awaiting a ruling', data?.units_awaiting_declaration ?? null],
    ['projected_devices', `Devices projected over the next ${data?.horizon_months ?? horizon} months`, data?.projected_devices ?? null],
  ]

  return (
    <div style={{ padding: '18px 22px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>📦 Accessory Order Plan</h1>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>
            What to order for the next {data?.horizon_months ?? horizon} months, from the device models the
            stores actually sold over the last {data?.lookback_months ?? months} — once you have said
            which models are still current.
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <ExportButtons payload={buildPayload} />
          <SendReportButton title="Accessory Order Plan" label="📤 Send"
            exportPayload={buildPayload} />
        </div>
      </div>

      {/* ── The filters: stores, markets, and the two windows ───────────────────────────────────── */}
      <div className="card" style={{ padding: '12px 14px', marginBottom: 14, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>
          <div style={{ marginBottom: 4 }}>Look back</div>
          <select style={sel} value={months} onChange={e => { setLoading(true); setMonths(Number(e.target.value)) }}>
            {[1, 2, 3, 4, 6, 9, 12, 24].map(n =>
              <option key={n} value={n}>{n} month{n === 1 ? '' : 's'}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>
          <div style={{ marginBottom: 4 }}>Plan for the next</div>
          <select style={sel} value={horizon} onChange={e => { setLoading(true); setHorizon(Number(e.target.value)) }}>
            {[1, 2, 3, 4, 6, 12].map(n =>
              <option key={n} value={n}>{n} month{n === 1 ? '' : 's'}</option>)}
          </select>
        </label>
        <div style={{ minWidth: 220 }}>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Stores</div>
          <MultiSelect options={data?.store_options || []} value={selStores}
            onChange={v => { setLoading(true); setSelStores(v) }} allLabel="All stores" width={220} searchable />
        </div>
        <div style={{ minWidth: 200 }}>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Markets</div>
          <MultiSelect options={data?.markets || []} value={selMarkets}
            onChange={v => { setLoading(true); setSelMarkets(v) }} allLabel="All markets" width={200} />
        </div>
        <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 320 }}>
          Months read: {(data?.months || []).join(' · ') || '—'}
        </div>
      </div>

      {err && <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: 'var(--c-bg-fee2e2)', color: 'var(--c-fg-991b1b)', fontSize: 13 }}>{err}</div>}

      {/* ── The four counted figures, each opening to the model rows behind it ──────────────────── */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        {tiles.map(([key, label, value]) => (
          <div key={key} className="card" style={{ padding: '10px 14px', minWidth: 190 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>{label}</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{num(value)}</div>
          </div>
        ))}
      </div>
      <div style={{ marginBottom: 14 }}>
        <DrillTileLinks drills={drill.dl(...FIGURES.flatMap(([k, l]) => [k, l]))}
          open={drill.open} onToggle={drill.toggle} />
        {drill.panel && <DrillDownPanel panel={drill.panel} />}
      </div>

      {/* ── Why there is no order yet, when there is no order yet ───────────────────────────────── */}
      {data?.reason &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)', fontSize: 13 }}>
          <b>No order has been worked out yet.</b> {PLAN_WHY[data.reason] || data.reason}
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {/* ── THE ORDER, PER PHONE (owner 2026-10-30) ──────────────────────────────────────
              *"the order is per phone, so it should be not be on top as displayed, the accessory
              order should be per phone model and on one column and the cases type on the other
              columns"*. So the headline is the sheet, and the single total moved to the bottom. */}
          <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>🛒 Order by phone</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                {data?.projected_devices === null
                  ? 'Nothing yet — the rulings below decide it.'
                  : `${num(data?.projected_devices)} devices projected over ${data?.horizon_months} months · cases split by ${MIX_WHY[data?.case_mix_basis || ''] || data?.case_mix_basis}`}
              </div>
              <button className="btn" style={{ marginLeft: 'auto' }}
                onClick={() => setShowSetup(v => !v)}>
                {showSetup ? 'Hide setup' : '⚙︎ Mix & catalog'}
              </button>
            </div>

            {/* ── THE DECLARATIONS, EDITABLE HERE ──────────────────────────────────────────────
                *"the proption of blue black should not be hard coded and but an option editable by
                the user"* — it is a declaration, and this is where it is written. */}
            {showSetup && (
              <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '12px 14px', marginBottom: 14, background: 'var(--surface2)' }}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>Colour mix for cases</div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 8 }}>
                  Currently {MIX_WHY[data?.case_mix_basis || ''] || data?.case_mix_basis}. The shares
                  are rescaled to 100% if they do not add up, and the colour quantities always foot
                  to the case quantity.
                </div>
                {(mixDraft ?? data?.case_mix ?? []).map((g, i) => (
                  <div key={g.key} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                    <input style={{ ...sel, width: 220 }} value={g.label}
                      onChange={e => setMixDraft(d => (d ?? data?.case_mix ?? []).map((x, j) => j === i ? { ...x, label: e.target.value } : x))} />
                    <input style={{ ...sel, width: 90 }} type="number" min={0} max={100}
                      value={Math.round((g.share || 0) * 1000) / 10}
                      onChange={e => setMixDraft(d => (d ?? data?.case_mix ?? []).map((x, j) => j === i ? { ...x, share: Number(e.target.value) / 100 } : x))} />
                    <span style={{ fontSize: 12, color: 'var(--text3)' }}>%</span>
                    <button className="btn" style={{ fontSize: 11 }}
                      onClick={() => setMixDraft(d => (d ?? data?.case_mix ?? []).filter((_, j) => j !== i))}>Remove</button>
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <button className="btn" style={{ fontSize: 11 }}
                    onClick={() => setMixDraft(d => [...(d ?? data?.case_mix ?? []), { key: `group_${((d ?? data?.case_mix ?? []).length) + 1}`, label: 'New colour group', share: 0, qty: null }])}>
                    + Colour group
                  </button>
                  <button className="btn" disabled={!mixDraft || saving}
                    onClick={() => void saveConfig({ case_mix: (mixDraft || []).map(g => ({ key: g.key, label: g.label, share: g.share })) })}>
                    {saving ? 'Saving…' : 'Save colour mix'}
                  </button>
                  {mixDraft && <button className="btn" style={{ fontSize: 11 }} onClick={() => setMixDraft(null)}>Cancel</button>}
                </div>

                {(data?.grid_columns || []).map(c => (
                  <div key={c.category} style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
                      {c.category_label} mix
                      <span style={{ fontWeight: 400, color: 'var(--text3)', marginLeft: 6 }}>
                        {data?.option_mix_bases?.[c.category] === 'even_split'
                          ? 'split evenly — nothing declared yet'
                          : 'your declared mix'}
                      </span>
                    </div>
                    {(styleDraft?.[c.category] ?? data?.option_mix?.[c.category] ?? []).map((o, i) => (
                      <div key={o.key} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                        <span style={{ fontSize: 12, width: 220 }}>{o.label}</span>
                        <input style={{ ...sel, width: 90 }} type="number" min={0} max={100}
                          value={Math.round((o.share || 0) * 1000) / 10}
                          onChange={e => setStyleDraft(d => ({
                            ...(d || {}),
                            [c.category]: (d?.[c.category] ?? data?.option_mix?.[c.category] ?? [])
                              .map((x, j) => j === i ? { ...x, share: Number(e.target.value) / 100 } : x),
                          }))} />
                        <span style={{ fontSize: 12, color: 'var(--text3)' }}>%</span>
                      </div>
                    ))}
                    <button className="btn" style={{ fontSize: 11, marginTop: 4 }}
                      disabled={saving || !styleDraft?.[c.category]}
                      onClick={() => void saveConfig({
                        option_mix: { ...(data?.option_mix || {}), ...(styleDraft || {}) },
                      })}>
                      {saving ? 'Saving…' : `Save ${c.category_label.toLowerCase()} mix`}
                    </button>
                  </div>
                ))}

                <div style={{ fontSize: 12, fontWeight: 700, margin: '16px 0 6px' }}>Catalog</div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 8 }}>
                  Add a style, a type, a wattage or a connector and it appears as a column or a
                  dropdown straight away. A picture is optional.
                </div>
                {(data?.catalog || []).map(c => (
                  <div key={c.key} style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>
                      {c.label}
                      <span style={{ fontWeight: 400, color: 'var(--text3)', marginLeft: 6 }}>
                        {c.grain === 'split' ? 'per phone, split across the styles'
                          : 'one per phone, consolidated across phones'}
                        {c.per_device === null ? ' · no rate declared, so it is not ordered' : ` · ${c.per_device} per device`}
                      </span>
                    </div>
                    {c.dimensions.map(d => (
                      <div key={d.key} style={{ margin: '4px 0 0 12px' }}>
                        <div style={{ fontSize: 11, color: 'var(--text2)' }}>
                          {d.label}: {d.options.map(o => o.label).join(' · ') || '—'}
                        </div>
                        <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                          <input style={{ ...sel, width: 180 }} placeholder={`Add a ${d.label.toLowerCase()}`}
                            value={newOpt[`${c.key}|${d.key}`]?.label || ''}
                            onChange={e => setNewOpt(n => ({ ...n, [`${c.key}|${d.key}`]: { label: e.target.value, image: n[`${c.key}|${d.key}`]?.image || '' } }))} />
                          <input style={{ ...sel, width: 220 }} placeholder="Picture URL (optional)"
                            value={newOpt[`${c.key}|${d.key}`]?.image || ''}
                            onChange={e => setNewOpt(n => ({ ...n, [`${c.key}|${d.key}`]: { label: n[`${c.key}|${d.key}`]?.label || '', image: e.target.value } }))} />
                          <button className="btn" style={{ fontSize: 11 }} disabled={saving || !newOpt[`${c.key}|${d.key}`]?.label?.trim()}
                            onClick={() => addOption(c.key, d.key)}>Add</button>
                        </div>
                      </div>
                    ))}
                  </div>
                ))}

                <div style={{ fontSize: 12, fontWeight: 700, margin: '16px 0 6px' }}>Colour words in your product descriptions</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                  {data?.colour_tokens_basis === 'declared'
                    ? <>These are the words stripped out so one phone is one row: <b>{(data?.colour_tokens || []).join(', ')}</b>. Add one if a new colour appears and the same phone starts showing twice.</>
                    : <>None declared, so every colour of a phone is still its own row. Add your feed&rsquo;s colour words here to combine them.</>}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                  <input style={{ ...sel, width: 420 }} placeholder="black, blue, midnight, pink, …"
                    defaultValue={(data?.colour_tokens || []).join(', ')}
                    onBlur={e => {
                      const next = e.target.value.split(',').map(t => t.trim()).filter(Boolean)
                      if (next.join('|') !== (data?.colour_tokens || []).join('|')) void saveConfig({ colour_tokens: next })
                    }} />
                  <span style={{ fontSize: 11, color: 'var(--text3)', alignSelf: 'center' }}>saved when you click away</span>
                </div>
              </div>
            )}

            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                    <th style={{ padding: '4px 6px' }}>Phone</th>
                    <th style={{ padding: '4px 6px', textAlign: 'right' }}>Projected</th>
                    {(data?.grid_columns || []).flatMap(c => [
                      ...c.options.map(o => (
                        <th key={`${c.category}:${o.key}`} style={{ padding: '4px 6px', textAlign: 'right' }}
                          title={`${c.category_label} — ${o.label}`}>
                          {/* The picture is a URL the user typed for their own catalog, on no
                              host this app knows, so next/image cannot be configured for it. */}
                          {o.image_url
                            ? // eslint-disable-next-line @next/next/no-img-element
                              <img src={o.image_url} alt={o.label} width={18} height={18}
                                style={{ verticalAlign: 'middle', marginRight: 4, borderRadius: 3, objectFit: 'cover' }} />
                            : null}
                          {o.label}
                        </th>
                      )),
                      <th key={`${c.category}:total`} style={{ padding: '4px 6px', textAlign: 'right', borderLeft: '1px solid var(--border)' }}>
                        {c.category_label} total
                      </th>,
                    ])}
                  </tr>
                </thead>
                <tbody>
                  {(data?.model_orders || []).map(r => (
                    <Fragment key={r.model}>
                      <tr style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '6px' }}>
                          {r.variants.length > 1 &&
                            <button className="btn" style={{ fontSize: 10, padding: '0 5px', marginRight: 6 }}
                              onClick={() => setOpenRow(k => k === r.model ? '' : r.model)}
                              title="Show the colours that were actually sold">
                              {openRow === r.model ? '▾' : '▸'}
                            </button>}
                          <span style={{ color: 'var(--text3)', marginRight: 6 }}>#{r.rank}</span>{r.model}
                          {r.variants.length > 1 &&
                            <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>
                              ({r.variants.length} colours)
                            </span>}
                        </td>
                        <td style={{ padding: '6px', textAlign: 'right' }}
                          title={WHY[r.projection_basis] || r.projection_basis}>
                          {r.projected_units === null
                            ? <span style={{ color: 'var(--text3)' }}>— awaiting</span>
                            : <b>{num(r.projected_units)}</b>}
                        </td>
                        {(data?.grid_columns || []).flatMap(c => [
                          ...c.options.map(o => {
                            const q = r.cells[c.category]?.options.find(x => x.key === o.key)
                            return (
                              <td key={`${c.category}:${o.key}`} style={{ padding: '6px', textAlign: 'right' }}
                                title={`${o.label} — ${share(q?.share)} of this phone's ${c.category_label.toLowerCase()} order`}>
                                {num(q?.qty ?? null)}
                              </td>
                            )
                          }),
                          <td key={`${c.category}:total`} style={{ padding: '6px', textAlign: 'right', fontWeight: 700, borderLeft: '1px solid var(--border)' }}>
                            {num(r.cells[c.category]?.qty ?? null)}
                          </td>,
                        ])}
                      </tr>
                      {openRow === r.model && (
                        <tr style={{ background: 'var(--surface2)' }}>
                          <td colSpan={2 + (data?.grid_columns || []).reduce((n, c) => n + c.options.length + 1, 0)}
                            style={{ padding: '8px 10px', fontSize: 12 }}>
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>What was actually sold under this phone</div>
                            {r.variants.map(v => (
                              <div key={v.model} style={{ color: 'var(--text2)' }}>
                                {v.units_total} × {v.model}
                              </div>
                            ))}
                            {r.cells.case?.colours &&
                              <div style={{ marginTop: 6, color: 'var(--text2)' }}>
                                Cases by colour: {r.cells.case.colours.map(c => `${c.label} ${num(c.qty)}`).join('  ·  ')}
                              </div>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {(data?.model_orders?.length || 0) === 0 &&
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>
                No device sales in this window for these stores.
              </div>}
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
              ℹ︎ Each style column is a share of that phone&rsquo;s order and the columns add up to the
              total. A dash means the order cannot be worked out yet, never a quantity of zero.
              {(data?.option_mix_bases?.case === 'even_split') &&
                ' The styles are split evenly because no style mix has been declared — set one under Mix & catalog.'}
            </div>
          </div>

          {/* ── CONSOLIDATED: cables, cubes, earphones ───────────────────────────────────────────
              *"charger and cables should be consolidated based on the sales, not per phone ... the
              cables are common to a lot of phones"*. One line per connector, with the phones it
              covers and the dropdown that moves a phone to another one. */}
          {(data?.consolidated || []).map(c => (
            <div key={c.category} className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 700 }}>🔌 {c.category_label} — consolidated</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                  {c.reason === 'no_rate'
                    ? 'No rate declared, so none is being ordered. Set how many per device to start ordering these.'
                    : `${num(c.qty)} in total, across every phone that takes the same one`}
                </div>
              </div>
              {c.lines.length > 0 && (
                <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                      <th style={{ padding: '4px 6px' }}>Specification</th>
                      <th style={{ padding: '4px 6px', textAlign: 'right' }}>Quantity</th>
                      <th style={{ padding: '4px 6px' }}>Phones it covers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.lines.map(l => (
                      <tr key={l.label} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '6px' }}>
                          {l.label}
                          {l.spec_basis === 'house_default' &&
                            <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>(standard)</span>}
                        </td>
                        <td style={{ padding: '6px', textAlign: 'right', fontWeight: 700 }}>{num(l.qty)}</td>
                        <td style={{ padding: '6px', color: 'var(--text2)', fontSize: 12 }}>
                          {l.models.map(m => `${m.model} (${m.qty})`).join('  ·  ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {/* THE PER-PHONE DROPDOWN — the standard applies until somebody changes it for one
                  phone, which is what the owner asked for. */}
              {c.lines.length > 0 && (
                <details style={{ marginTop: 10 }}>
                  <summary style={{ fontSize: 12, cursor: 'pointer', color: 'var(--text2)' }}>
                    Change which {c.category_label.toLowerCase()} a phone takes
                  </summary>
                  <div style={{ marginTop: 8 }}>
                    {(data?.model_orders || []).filter(r => r.projected_units !== null).map(r => (
                      <div key={r.model} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 12, minWidth: 260 }}>{r.model}</span>
                        {c.dimensions.map(d => {
                          const k = `${c.category}|${d.key}|${r.model}`
                          const current = c.lines.find(l => l.models.some(m => m.model === r.model))?.spec?.[d.key] || d.default
                          return (
                            <select key={d.key} style={{ ...sel, fontSize: 12 }}
                              value={specDraft[k] ?? current}
                              onChange={e => setSpecDraft(s => ({ ...s, [k]: e.target.value }))}>
                              {d.options.map(o => <option key={o.key} value={o.key}>{o.label}</option>)}
                            </select>
                          )
                        })}
                      </div>
                    ))}
                    <button className="btn" style={{ marginTop: 8 }} disabled={saving || !Object.keys(specDraft).length}
                      onClick={() => void saveSpecs()}>
                      {saving ? 'Saving…' : `Save ${Object.keys(specDraft).length} assignment(s)`}
                    </button>
                  </div>
                </details>
              )}
              {c.awaiting.length > 0 &&
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                  ℹ︎ {c.awaiting.length} phone(s) are not in this total yet
                  {c.reason === 'no_rate' ? '.' : ' because nobody has ruled on them.'}
                </div>}
            </div>
          ))}

          {/* ── THE TOTALS, now at the bottom (owner 2026-10-30: *"it should be not be on top as
              displayed"*) — one number per kind, for the buyer who wants the summary beside the
              sheet that breaks it down. */}
          <div className="card" style={{ padding: '14px 16px', marginBottom: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Σ Totals across every phone</div>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                  <SortableTh field="label" sort={acc.sort} onSort={acc.toggle}>Accessory</SortableTh>
                  <SortableTh field="per_device" sort={acc.sort} onSort={acc.toggle} style={{ textAlign: 'right' }}>Per device</SortableTh>
                  <SortableTh field="qty" sort={acc.sort} onSort={acc.toggle} style={{ textAlign: 'right' }}>Quantity to order</SortableTh>
                  <th style={{ padding: '4px 6px' }}>Colour split</th>
                </tr>
              </thead>
              <tbody>
                {acc.sorted.map(r => (
                  <tr key={r.key} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px' }}>{r.label}</td>
                    <td style={{ padding: '6px', textAlign: 'right' }}>{r.per_device}</td>
                    <td style={{ padding: '6px', textAlign: 'right', fontWeight: 700 }}>{num(r.qty)}</td>
                    <td style={{ padding: '6px', color: 'var(--text2)' }}>
                      {r.colours
                        ? r.colours.map(c => `${c.label} ${share(c.share)}: ${num(c.qty)}`).join('  ·  ')
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ── THE MODELS, BEST SELLER FIRST, EACH AWAITING A RULING ─────────────────────────── */}
          <div className="card" style={{ padding: '14px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>📱 Best sellers — is each one current or end of life?</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                {num(data?.source_meta?.models)} models · {share(data?.declared_share)} of the window&rsquo;s
                demand ruled on
              </div>
              <button className="btn" disabled={!dirty || saving} onClick={saveRulings}
                style={{ marginLeft: 'auto' }}>
                {saving ? 'Saving…' : dirty ? `Save ${Object.keys(pending).length} ruling(s)` : 'Save rulings'}
              </button>
            </div>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase' }}>
                  <SortableTh field="model" sort={sort} onSort={toggle}>Model</SortableTh>
                  {(data?.months || []).map(p =>
                    <SortableTh key={p} field={`m:${p}`} sort={sort} onSort={toggle} style={{ textAlign: 'right' }}>{p}</SortableTh>)}
                  <SortableTh field="units_total" sort={sort} onSort={toggle} style={{ textAlign: 'right' }}>Total</SortableTh>
                  <SortableTh field="avg_monthly" sort={sort} onSort={toggle} style={{ textAlign: 'right' }}>Avg / month</SortableTh>
                  <SortableTh field="share" sort={sort} onSort={toggle} style={{ textAlign: 'right' }}>Share</SortableTh>
                  <SortableTh field="lifecycle" sort={sort} onSort={toggle}>Current or end of life?</SortableTh>
                  <SortableTh field="projected_units" sort={sort} onSort={toggle} style={{ textAlign: 'right' }}>Projected</SortableTh>
                </tr>
              </thead>
              <tbody>
                {sorted.map(r => (
                  <tr key={r.model} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px' }}>
                      <span style={{ color: 'var(--text3)', marginRight: 6 }}>#{r.rank}</span>{r.model}
                      {r.variant_count > 1 &&
                        <span title={r.variants.map(v => `${v.units_total} x ${v.model}`).join('\n')}
                          style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>
                          ({r.variant_count} colours)
                        </span>}
                      {r.history_basis === 'partial_history' &&
                        <span title={`Only selling for ${r.months_observed} of the ${data?.lookback_months} look-back months, so its average is over those months only.`}
                          style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>
                          (new — {r.months_observed} mo)
                        </span>}
                    </td>
                    {(data?.months || []).map(p => (
                      <td key={p} style={{ padding: '6px', textAlign: 'right' }}>
                        {num(r.units_by_month.find(m => m.period === p)?.units ?? 0)}
                      </td>
                    ))}
                    <td style={{ padding: '6px', textAlign: 'right', fontWeight: 700 }}>{num(r.units_total)}</td>
                    <td style={{ padding: '6px', textAlign: 'right' }}>{num(r.avg_monthly, 1)}</td>
                    <td style={{ padding: '6px', textAlign: 'right' }}>{share(r.share)}</td>
                    <td style={{ padding: '6px' }}>
                      <select style={sel} value={stateOf(r)}
                        onChange={e => setPending(p => ({ ...p, [r.model]: e.target.value }))}>
                        <option value="">— not ruled on —</option>
                        {(data?.lifecycle_states || []).map(s =>
                          <option key={s} value={s}>{data?.lifecycle_labels?.[s] || s}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '6px', textAlign: 'right' }}
                      title={WHY[r.projection_basis] || r.projection_basis}>
                      {r.projected_units === null
                        ? <span style={{ color: 'var(--text3)' }}>— awaiting</span>
                        : <b>{num(r.projected_units)}</b>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 &&
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
                No device sales in this window for these stores.
              </div>}
            {(data?.models_awaiting_declaration?.length || 0) > 0 &&
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                ℹ︎ {data?.models_awaiting_declaration.length} model(s) carrying{' '}
                {num(data?.units_awaiting_declaration)} units are not in the order because nobody has
                ruled on them yet. They are not treated as retired — mark them and the order grows.
              </div>}
          </div>
        </>
      )}
    </div>
  )
}
