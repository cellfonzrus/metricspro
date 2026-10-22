// FORECAST MATRIX — "how many phones of a particular model does each store need?" (owner request
// 2026-10-22: *"the forecasting should summarize in grid format to be exported to understand how many
// phones are needed of a particular model per store"*).
//
// The forecast endpoint answers at carrier × store × model grain, one row each, which is the right
// shape to reason about a single line and the wrong shape to ORDER from: a buyer needs one column per
// model and one row per store, so a purchase order reads straight off it.
//
// THIS MODULE IS THE PIVOT, AND NOTHING ELSE. It derives no figure — every cell is a SUM of the rows
// the page already loaded and already filtered, so the grid can never disagree with the list above it
// (pivot the filtered rows, never re-fetch). It is framework-free so it is unit-provable
// (`frontend/scratchpad/prove_forecast_matrix.mjs`) and so the SAME pivot builds the on-screen grid and
// the export — what-you-see-is-what-exports, RULE FOUR.
//
// A STORE IS KEYED ON ITS IDENTITY, NEVER ITS SPELLING. Rows carry `store_key` (the canonical store
// address resolved server-side through `account.store_identity`, §64) alongside the feed's own
// `store` spelling. Two spellings of one physical store are ONE row of this grid because they share a
// key — pivoting on the raw spelling would print the same store twice and split its order between the
// two halves, which is exactly the defect the store FILTER had.

export type ForecastRow = {
  store?: string | null
  store_key?: string | null
  device_model?: string | null
  carrier?: string | null
  units?: number | null
  on_hand?: number | null
  projected_demand?: number | null
  recommend_order?: number | null
}

/** The figures a cell can show. `order` is the default — it is the question the grid was asked. */
export type ForecastMetric = 'recommend_order' | 'projected_demand' | 'units' | 'on_hand'

export const FORECAST_METRICS: { id: ForecastMetric; label: string; tip: string }[] = [
  { id: 'recommend_order', label: 'Units to order', tip: 'Projected demand minus what is on hand' },
  { id: 'projected_demand', label: 'Projected demand', tip: 'Velocity × the days you are ordering for' },
  { id: 'units', label: 'Sold in window', tip: 'Units sold inside the sales window' },
  { id: 'on_hand', label: 'On hand', tip: 'Unsold stock on the shelf now (a snapshot, not windowed)' },
]

export type MatrixStore = { key: string; label: string; market?: string | null }
export type ForecastMatrix = {
  models: string[]                       // the columns, left to right
  stores: MatrixStore[]                  // the rows, top to bottom
  /** units at (store key, model); 0 when that store needs none of that model. */
  cell: (storeKey: string, model: string) => number
  storeTotal: (storeKey: string) => number
  modelTotal: (model: string) => number
  grandTotal: number
  /** True when every cell is 0 — "nothing to order", which is an ANSWER, not an empty grid. */
  empty: boolean
}

const norm = (v: unknown): string => (v == null ? '' : String(v)).trim()
const num = (v: unknown): number => { const n = Number(v); return Number.isFinite(n) ? n : 0 }

/** The label a store row is shown under. The canonical key is the identity; the first spelling seen is
 *  what a human recognises, so that is what is printed — the same split the filter options make. */
const UNASSIGNED = '(unassigned)'

/** PURE: pivot already-filtered forecast rows into stores × models.
 *
 *  `models` are ordered by how many units they account for overall (the model you must order most of
 *  reads first), ties broken by name so the column order is stable between renders and exports.
 *  `stores` are ordered by their own total for the same reason, with the unassigned bucket LAST —
 *  never dropped, because a row whose store could not be resolved is stock somebody still has to
 *  order, and hiding it would quietly shrink the order.
 */
export function buildForecastMatrix(rows: ForecastRow[], metric: ForecastMetric = 'recommend_order'): ForecastMatrix {
  const cells = new Map<string, number>()            // `${storeKey}\u0000${model}` -> units
  const storeMeta = new Map<string, MatrixStore>()
  const storeTot = new Map<string, number>()
  const modelTot = new Map<string, number>()
  let grand = 0

  for (const r of rows || []) {
    const model = norm(r.device_model)
    if (!model) continue                             // a row with no model names no column to order under
    const key = norm(r.store_key) || norm(r.store) || UNASSIGNED
    if (!storeMeta.has(key)) storeMeta.set(key, { key, label: norm(r.store) || UNASSIGNED })
    const v = num(r[metric])
    cells.set(`${key}\u0000${model}`, (cells.get(`${key}\u0000${model}`) || 0) + v)
    storeTot.set(key, (storeTot.get(key) || 0) + v)
    modelTot.set(model, (modelTot.get(model) || 0) + v)
    grand += v
  }

  const models = [...modelTot.keys()].sort((a, b) => (modelTot.get(b)! - modelTot.get(a)!) || a.localeCompare(b))
  const stores = [...storeMeta.values()].sort((a, b) => {
    if ((a.label === UNASSIGNED) !== (b.label === UNASSIGNED)) return a.label === UNASSIGNED ? 1 : -1
    return (storeTot.get(b.key)! - storeTot.get(a.key)!) || a.label.localeCompare(b.label)
  })

  return {
    models, stores,
    cell: (storeKey, model) => cells.get(`${norm(storeKey)}\u0000${norm(model)}`) || 0,
    storeTotal: storeKey => storeTot.get(norm(storeKey)) || 0,
    modelTotal: model => modelTot.get(norm(model)) || 0,
    grandTotal: grand,
    empty: grand === 0,
  }
}

/** An export row: the store's label, the row total, and one unit count per model column. The model
 *  properties are named by the model itself, so the type is the union of a label and the counts —
 *  `__store` / `__total` are reserved and can never be a model name. */
export type MatrixExportRow = { __store: string; __total: number } & Record<string, string | number>

/** The export COLUMN shape, structurally the `ExportColumn` the exporter takes — declared here so
 *  this file stays framework-free (and therefore provable) without importing that type. */
export type MatrixExportColumn = {
  header: string; field: string; role?: string
  get: (r: MatrixExportRow) => string | number
}

/** PURE: the matrix as export ROWS — one object per store, a property per model, plus the row total.
 *  The export and the on-screen grid read the SAME pivot, so a column can never appear in one and
 *  not the other. Property names are the model names; `__store` / `__total` are reserved. */
export function matrixExportRows(m: ForecastMatrix): MatrixExportRow[] {
  return m.stores.map(s => {
    const row: MatrixExportRow = { __store: s.label, __total: m.storeTotal(s.key) }
    for (const model of m.models) row[model] = m.cell(s.key, model)
    return row
  })
}

/** PURE: the export COLUMN spec for `matrixExportRows` — Store, one per model, Total. Shaped as
 *  `ExportColumn`s without importing the type, so this file stays framework-free and provable. */
export function matrixExportColumns(m: ForecastMatrix): MatrixExportColumn[] {
  return [
    { header: 'Store', field: 'store', role: 'store', get: r => r.__store },
    ...m.models.map(model => ({ header: model, field: model, get: (r: MatrixExportRow) => r[model] ?? 0 })),
    { header: 'Total', field: '__total', get: (r: MatrixExportRow) => r.__total },
  ]
}
