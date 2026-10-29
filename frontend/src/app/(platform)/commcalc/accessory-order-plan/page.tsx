'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
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

// ACCESSORY ORDER PLAN (index §COMMCALC-48, owner ask 2026-10-29) — the device models the stores
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
// WHERE EACH FIGURE CAME FROM (§CORE-17.3y). The four counted figures open to the model rows behind
// them, declared in `commcalc/accessory_order_drilldown.py` and folded once by
// `core/report_drilldown`. The rates — the declared share and the per-month run rate — are refused a
// panel (§CORE-17.3h), and so are the accessory quantities, which are the device total times a
// declared rate and therefore have no rows of their own (§CORE-17.3k).
//
// EVERY FILTER IS A QUERY PARAMETER, so the server folds figure and panel together on each change
// and `lib/drill-narrow` is used only for the agree-to-the-cent gate, never to narrow a panel.

type MonthUnits = { period: string; units: number }
type ModelRow = {
  model: string; rank: number
  units_by_month: MonthUnits[]
  units_total: number; months_observed: number; avg_monthly: number | null
  share: number; lifecycle: string; lifecycle_label: string
  history_basis: string; projected_units: number | null; projection_basis: string
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
  case_mix: Colour[]; case_mix_basis: string
  attach_plan: { key: string; label: string; per_device: number; colour_split: boolean }[]
  lifecycle_states: string[]; lifecycle_labels: Record<string, string>
  reason: string | null
  store_options: string[]; markets: string[]
  params: Record<string, unknown>
  source_meta: { device_lines: number; models: number }
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
        { name: 'Order', rows: accRows, columns: [
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
          {/* ── THE ORDER ─────────────────────────────────────────────────────────────────────── */}
          <div className="card" style={{ padding: '14px 16px', marginBottom: 22 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>🛒 What to order</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                {data?.projected_devices === null
                  ? 'Nothing yet — the rulings below decide it.'
                  : `${num(data?.projected_devices)} devices projected (${num(data?.projected_devices_per_month, 1)} a month), cases split by ${MIX_WHY[data?.case_mix_basis || ''] || data?.case_mix_basis}`}
              </div>
            </div>
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
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
              ℹ︎ The colour quantities always add up to the case quantity. A dash means the order
              cannot be worked out yet, never a quantity of zero.
            </div>
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
