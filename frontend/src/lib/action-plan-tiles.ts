// action-plan-tiles — THE ONE HOME for "how the Daily Action Plan is grouped into tiles".
//
// OWNER ASK 2026-11-10, verbatim: *"Need to organize the daily action plan to a more presentable
// way, it is right now a long report, collapse it per store, give our standard filters for store rep
// market date range, which metrics needs to be improved / all / family plan etc. Make it tiled with
// focused metrics — cells on the all line metrics and it shows who needs improvement and so forth
// with the details at the bottom, the tiles should be switched between kpi / stores / sales reps"*.
//
// ── WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT ─────────────────────────────────────────
//
// It is a GROUPING of verdicts somebody else already reached. Every number it reads was decided by
// `GET /commcalc/targets/{period}/action-plan`: the per-category catch-up and its severity come from
// `targets_engine.build_action_items`, a rep's KPI pass/fail from `kpi_failing.evaluate`, the
// at-risk dollars from the calculated commission rows, and the team roll-up from
// `targets_drilldown.team_totals`. None of that is re-derived here, because two paths answering the
// same question is the duplicate defect the house rules forbid.
//
// In particular THERE IS NO SECOND FOLD OF MONEY OR TARGETS. A tile's headline figure is the
// CARDINALITY of the list it prints — how many stores (or reps) need improvement — which is honest
// under any narrowing the filters apply. The achieved/target roll-up keeps its one home on the
// server and the page reads that fold directly.
//
// ── THE VOCABULARY IS THE TENANT'S, AND IT IS A REQUIRED ARGUMENT (directive 2026-10-30) ─────────
//
// There is no built-in metric list in this file. `planMetrics` reads what the endpoint declared for
// THIS org — the store categories that tenant put on Count Fields, and the KPIs that tenant's pay
// engine scores — and `buildTiles` takes those declarations as a required argument. A caller that
// has not asked the org cannot answer: with no declarations there are no tiles, never house words.
// One tenant's declarations over another tenant's plan therefore produce NOTHING rather than a tile
// labelled with the wrong tenant's words.
//
// ── NOT MEASURED IS NOT ZERO ─────────────────────────────────────────────────────────────────────
//
// The endpoint already separates "this rep failed the KPI" from "nothing fed this KPI"
// (`commission.kpis_no_data`). A tile over an unfed metric says so and carries no figure: "0 reps
// behind" reads as a clean sheet, and that is the shape that hides a broken feed — live right now,
// with the DLAR gross adds / activations / upgrades columns empty after the Elevate Go renames.
//
// PURE: no React, no fetch, no clock. Unit-proved by `src/lib/__tests__/action-plan-tiles.test.ts`;
// the wiring (the page dereferences this and holds no second grouping of its own) is locked by
// `backend/harness_action_plan_tiles.py`.
import { matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { SnapshotTile } from '@/lib/tile-snapshot'

// ── THE PAYLOAD, AS THE ENDPOINT SERVES IT ───────────────────────────────────────────────────────
// These types live here rather than in the page so that the fold and the screen cannot disagree
// about the shape; a rename on the Python side breaks one file, not several.
export type Sev = 'critical' | 'warning' | 'good'

export interface PlanItem { severity: Sev; metric: string; title: string; detail: string }
export interface PlanConversion {
  boxes: number; billpays: number; rate: number; target: number; meets_target: boolean
}
/** WHAT A SCOPE IS TRENDING TO on one metric, as the server decided it (§COMMCALC-71). `projected`
 *  is null when no day of the month has completed — a run rate off a part-day is not a trend — and
 *  `kind` is then 'not_measured', never 'short'. Nothing here is re-derived on the screen: the
 *  factor has one home and the Daily Targets page already prints its answer. */
export interface PlanTrend {
  projected: number | null
  kind: 'on_track' | 'short' | 'no_target' | 'not_measured'
  gap: number | null
  elapsed?: number; days_in_month?: number; basis?: string; complete?: boolean
}
export interface PlanMetric {
  cat: string; label: string; unit: string
  target: number; achieved: number | null; need: number; pace: number; today_target: number
  setup_fee_mtd?: number
  trend?: PlanTrend | null
}
export interface PlanKpi { kpi: string; label: string; target: number; actual: number; met: boolean }
/** A KPI with a target but NO recorded value — reported by the backend, never counted as failing. */
export interface PlanKpiNoData { kpi: string; label: string; target: number }
export interface PlanCommission {
  tier: number; kpis_met: number; total_kpis: number
  subtotal: number | null; total_payout: number; at_risk: number
  short_kpis: string[]; kpis: PlanKpi[]; kpis_no_data?: PlanKpiNoData[]
  t100: number; t75: number
}
export interface PlanRep {
  rep: string; conversion: PlanConversion; below_store: boolean
  items: PlanItem[]; commission: PlanCommission | null
}
export interface PlanStore {
  store_code: string; address?: string | null; market?: string | null
  conversion: PlanConversion; metrics: PlanMetric[]; items: PlanItem[]; reps: PlanRep[]
  commission_at_risk: number; has_target_row?: boolean
  counts: { critical: number; warning: number }
}

// ── THE DECLARED AXIS ────────────────────────────────────────────────────────────────────────────
/** 'store' = a target category this tenant declared; 'kpi' = a KPI this tenant's pay engine scores. */
export type MetricGrain = 'store' | 'kpi'
export type MetricDecl = { key: string; grain: MetricGrain; label: string; unit: string }

/** The composite key. A store category and a rep KPI may legitimately share a word ("familyplan"
 *  as a count target and as a percentage KPI are different questions), so the grain is part of the
 *  identity rather than something a caller has to remember to carry alongside. */
export function metricKey(grain: MetricGrain, key: string): string {
  return `${grain}:${key}`
}

/** THIS TENANT'S FOCUSABLE METRICS, read off their own plan — the metric picker's whole option list.
 *  Order is the order the tenant's own axis produced (store categories as `targets_engine` lists
 *  them, then the KPIs the pay engine scored), so the picker reads like their Count Fields page.
 *  A KPI that arrived only as `kpis_no_data` IS declared: it is a metric this tenant measures, and
 *  the tile's job is to report that nothing fed it. */
export function planMetrics(stores: PlanStore[]): MetricDecl[] {
  const seen = new Map<string, MetricDecl>()
  for (const s of stores || []) {
    for (const m of s?.metrics || []) {
      if (!m?.cat) continue
      const k = metricKey('store', m.cat)
      if (!seen.has(k)) seen.set(k, { key: k, grain: 'store', label: m.label || m.cat, unit: m.unit || 'count' })
    }
  }
  for (const s of stores || []) {
    for (const r of s?.reps || []) {
      const c = r?.commission
      if (!c) continue
      for (const k of c.kpis || []) {
        if (!k?.kpi) continue
        const key = metricKey('kpi', k.kpi)
        if (!seen.has(key)) seen.set(key, { key, grain: 'kpi', label: k.label || k.kpi, unit: 'percent' })
      }
      for (const k of c.kpis_no_data || []) {
        if (!k?.kpi) continue
        const key = metricKey('kpi', k.kpi)
        if (!seen.has(key)) seen.set(key, { key, grain: 'kpi', label: k.label || k.kpi, unit: 'percent' })
      }
    }
  }
  return [...seen.values()]
}

// ── THE TILES ────────────────────────────────────────────────────────────────────────────────────
export type TileView = 'kpi' | 'store' | 'rep'
export const TILE_VIEWS: { key: TileView; label: string }[] = [
  { key: 'kpi', label: 'KPI' },
  { key: 'store', label: 'Stores' },
  { key: 'rep', label: 'Sales reps' },
]

/** A TILE'S SEVERITY. `none` is not a fourth colour: it is the absence of a verdict, which is what
 *  an unmeasured metric has. It sorts last and renders grey. */
export type TileSev = Sev | 'none'

/** One line of "who needs improvement" — a store, a rep or a metric, with the gap in its own words. */
export type NeedsWork = { key: string; name: string; detail: string; severity: Sev }

export type Tile = {
  key: string
  label: string
  view: TileView
  /** false = nothing fed this tile, and `note` says so. Never rendered as a zero. */
  measured: boolean
  /** The headline figure: the count behind for a KPI/store tile, the at-risk dollars for a rep tile.
   *  `null` whenever it was not measured — an absent figure is blank, never 0. */
  figure: number | null
  money: boolean
  behind: number
  total: number
  severity: TileSev
  note: string
  unit: string
  needsWork: NeedsWork[]
}

const SEV_RANK: Record<TileSev, number> = { critical: 0, warning: 1, good: 2, none: 3 }
const num = (v: unknown): number | null => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v))
const storeName = (s: PlanStore) => (s.address || s.store_code || '').trim() || s.store_code

/** A figure in the unit the TENANT declared for it. No currency symbol is assumed: `dollars` is a
 *  unit the axis declares, exactly as the targets screens read it. */
export function metricText(n: number | null | undefined, unit: string): string {
  if (n == null) return ''
  if (unit === 'dollars') return '$' + Math.round(n).toLocaleString()
  if (unit === 'percent') return `${Math.round(n * 10) / 10}%`
  return String(Math.round(n * 10) / 10)
}

const worst = (list: NeedsWork[]): TileSev =>
  list.length ? (list.some(w => w.severity === 'critical') ? 'critical' : 'warning') : 'good'

/** The DECLARED metrics a picker selection narrows to. An empty selection is "all of them"; a key
 *  this tenant never declared selects nothing, and a selection of ONLY such keys narrows to nothing
 *  rather than widening back to every metric — a filter that silently stops filtering is a lie. */
function focused(decls: MetricDecl[], selected: string[]): MetricDecl[] {
  const d = decls || []
  if (!selected || selected.length === 0) return d
  const want = new Set(selected)
  return d.filter(x => want.has(x.key))
}

/** Each rep ONCE, with the stores they appear in. The endpoint keys commission rows by REP, so a rep
 *  working two stores carries the SAME row on both: summing their at-risk dollars would invent money
 *  that is not at risk. The first non-null commission is therefore taken, never added up. */
function repsFolded(stores: PlanStore[]) {
  const out = new Map<string, { rep: string; stores: string[]; commission: PlanCommission | null; items: PlanItem[] }>()
  for (const s of stores || []) {
    for (const r of s?.reps || []) {
      const name = (r.rep || '').trim()
      if (!name) continue
      const cur = out.get(name.toUpperCase())
      if (!cur) {
        out.set(name.toUpperCase(), { rep: name, stores: [storeName(s)], commission: r.commission || null, items: [...(r.items || [])] })
      } else {
        if (!cur.stores.includes(storeName(s))) cur.stores.push(storeName(s))
        if (!cur.commission) cur.commission = r.commission || null
        cur.items.push(...(r.items || []))
      }
    }
  }
  return [...out.values()]
}

/** The severity the SERVER gave this store for this category, when it gave one. A store that is
 *  short but carries no item for the category still needs improvement, so it is a warning — the
 *  shortfall is the server's `need`, not a verdict invented here. */
function storeSeverityFor(s: PlanStore, cat: string): Sev {
  const it = (s.items || []).find(x => x.metric === cat && x.severity !== 'good')
  return it ? it.severity : 'warning'
}

function kpiCells(stores: PlanStore[], kpi: string) {
  const measured: { rep: string; entry: PlanKpi }[] = []
  const unfed: string[] = []
  for (const r of repsFolded(stores)) {
    const c = r.commission
    if (!c) continue
    const hit = (c.kpis || []).find(k => k.kpi === kpi)
    if (hit) { measured.push({ rep: r.rep, entry: hit }); continue }
    if ((c.kpis_no_data || []).some(k => k.kpi === kpi)) unfed.push(r.rep)
  }
  return { measured, unfed }
}

function kpiTile(stores: PlanStore[], d: MetricDecl): Tile | null {
  const key = d.key.slice('kpi:'.length)
  const { measured, unfed } = kpiCells(stores, key)
  const needsWork: NeedsWork[] = measured
    .filter(m => !m.entry.met)
    .sort((a, b) => (b.entry.target - b.entry.actual) - (a.entry.target - a.entry.actual))
    .map(m => ({
      key: m.rep, name: m.rep, severity: 'warning' as Sev,
      detail: `${metricText(m.entry.actual, 'percent')} vs ${metricText(m.entry.target, 'percent')} target`,
    }))
  const total = measured.length
  const behind = needsWork.length
  // NOTHING IN THIS PLAN CARRIES THE METRIC — no scored value and no declared no-data. There is no
  // tile, because a tile would be this declaration answering for rows that never offered it: the
  // shape that lets one tenant's vocabulary (or a stale picker) label another tenant's screen.
  if (total === 0 && !unfed.length) return null
  if (total === 0) {
    return {
      key: d.key, label: d.label, view: 'kpi', measured: false, figure: null, money: false,
      behind: 0, total: 0, severity: 'none', unit: d.unit, needsWork: [],
      note: `No recorded value for ${d.label} this period — ${unfed.length} rep(s) have the target and no feed landed a figure, so this is not measured rather than nobody being behind.`,
    }
  }
  // EVERY measured rep below the bar is not the same thing as one of them: when nobody is meeting a
  // KPI the cause is usually the bar or the feed, not the people, and the tile says so by colour.
  const severity: TileSev = behind === 0 ? 'good' : behind === total ? 'critical' : 'warning'
  const tail = unfed.length ? ` ${unfed.length} rep(s) have no recorded value and are not counted.` : ''
  return {
    key: d.key, label: d.label, view: 'kpi', measured: true, figure: behind, money: false,
    behind, total, severity, unit: d.unit, needsWork,
    note: behind === 0
      ? `Every measured rep is at or above the ${metricText(measured[0].entry.target, 'percent')} target.${tail}`
      : `${behind} of ${total} rep(s) below target — worst: ${needsWork[0].name} (${needsWork[0].detail}).${tail}`,
  }
}

function storeMetricTile(stores: PlanStore[], d: MetricDecl): Tile | null {
  const cat = d.key.slice('store:'.length)
  const carrying: { store: PlanStore; m: PlanMetric }[] = []
  const unfed: string[] = []
  for (const s of stores || []) {
    const m = (s.metrics || []).find(x => x.cat === cat)
    if (!m || (num(m.target) || 0) <= 0) continue
    if (num(m.achieved) == null) { unfed.push(storeName(s)); continue }
    carrying.push({ store: s, m })
  }
  const needsWork: NeedsWork[] = carrying
    .filter(c => (num(c.m.need) || 0) > 0)
    .sort((a, b) => (num(b.m.need) || 0) - (num(a.m.need) || 0))
    .map(c => ({
      key: c.store.store_code, name: storeName(c.store),
      severity: storeSeverityFor(c.store, cat),
      detail: `${metricText(num(c.m.need), d.unit)} to go`,
    }))
  const total = carrying.length
  const behind = needsWork.length
  // Same rule as the KPI half: no store in this plan declared the category and none is waiting on a
  // figure for it, so there is nothing here to tile.
  if (total === 0 && !unfed.length) return null
  if (total === 0) {
    return {
      key: d.key, label: d.label, view: 'kpi', measured: false, figure: null, money: false,
      behind: 0, total: 0, severity: 'none', unit: d.unit, needsWork: [],
      note: `${unfed.length} store(s) have a ${d.label} target and no achieved figure landed, so this is not measured.`,
    }
  }
  const tail = unfed.length ? ` ${unfed.length} store(s) have no achieved figure and are not counted.` : ''
  return {
    key: d.key, label: d.label, view: 'kpi', measured: true, figure: behind, money: false,
    behind, total, severity: behind === 0 ? 'good' : worst(needsWork), unit: d.unit, needsWork,
    note: behind === 0
      ? `Every store with a ${d.label} target has met it.${tail}`
      : `${behind} of ${total} store(s) behind — worst: ${needsWork[0].name} (${needsWork[0].detail}).${tail}`,
  }
}

function storeTile(s: PlanStore, decls: MetricDecl[]): Tile {
  const needsWork: NeedsWork[] = []
  let total = 0
  for (const d of decls) {
    if (d.grain === 'store') {
      const cat = d.key.slice('store:'.length)
      const m = (s.metrics || []).find(x => x.cat === cat)
      if (!m || (num(m.target) || 0) <= 0 || num(m.achieved) == null) continue
      total++
      if ((num(m.need) || 0) > 0) {
        needsWork.push({
          key: d.key, name: d.label, severity: storeSeverityFor(s, cat),
          detail: `${metricText(num(m.need), d.unit)} to go`,
        })
      }
    } else {
      // A KPI AT STORE GRAIN IS ITS REPS' KPI. The store has no KPI row of its own, so the question
      // "is this store behind on Family Plan" is answered by the reps working it — the same measured
      // values the rep view reads, never a second score.
      const kpi = d.key.slice('kpi:'.length)
      const { measured } = kpiCells([s], kpi)
      if (!measured.length) continue
      total++
      const short = measured.filter(m => !m.entry.met)
      if (short.length) {
        needsWork.push({
          key: d.key, name: d.label, severity: 'warning',
          detail: `${short.length} of ${measured.length} rep(s) below ${metricText(measured[0].entry.target, 'percent')}`,
        })
      }
    }
  }
  const behind = needsWork.length
  if (total === 0) {
    return {
      key: s.store_code, label: storeName(s), view: 'store', measured: false, figure: null,
      money: false, behind: 0, total: 0, severity: 'none', unit: 'count', needsWork: [],
      note: `Not measured for ${storeName(s)} — none of the focused metrics has a declared target or a recorded value here, which is not the same as nothing to fix.`,
    }
  }
  const sev: TileSev = behind === 0 ? 'good'
    : (s.counts?.critical || 0) > 0 || needsWork.some(w => w.severity === 'critical') ? 'critical' : 'warning'
  const conv = s.conversion
  const convText = conv && (num(conv.billpays) || 0) > 0 ? ` Conversion ${metricText(num(conv.rate), 'percent')}.` : ''
  return {
    key: s.store_code, label: storeName(s), view: 'store', measured: true, figure: behind,
    money: false, behind, total, severity: sev, unit: 'count', needsWork,
    note: behind === 0
      ? `On track across all ${total} focused metric(s).${convText}`
      : `Behind on ${behind} of ${total} focused metric(s) — ${needsWork[0].name}: ${needsWork[0].detail}.${convText}`,
  }
}

function repTile(r: { rep: string; stores: string[]; commission: PlanCommission | null }, decls: MetricDecl[]): Tile {
  const kpiDecls = decls.filter(d => d.grain === 'kpi')
  const c = r.commission
  const where = r.stores.length > 1 ? ` Works ${r.stores.length} stores: ${r.stores.join(', ')}.` : ''
  if (!c) {
    return {
      key: r.rep, label: r.rep, view: 'rep', measured: false, figure: null, money: true,
      behind: 0, total: 0, severity: 'none', unit: 'dollars', needsWork: [],
      note: `Commission has not been calculated for ${r.rep} in this period, so there is no tier, no KPI score and no at-risk figure to show.${where}`,
    }
  }
  const needsWork: NeedsWork[] = []
  let total = 0
  const unfed: string[] = []
  for (const d of kpiDecls) {
    const kpi = d.key.slice('kpi:'.length)
    const hit = (c.kpis || []).find(k => k.kpi === kpi)
    if (!hit) {
      if ((c.kpis_no_data || []).some(k => k.kpi === kpi)) unfed.push(d.label)
      continue
    }
    total++
    if (!hit.met) {
      needsWork.push({
        key: d.key, name: d.label, severity: 'warning',
        detail: `${metricText(hit.actual, 'percent')} vs ${metricText(hit.target, 'percent')} target`,
      })
    }
  }
  const behind = needsWork.length
  const atRisk = num(c.at_risk) || 0
  const tail = unfed.length ? ` Not measured: ${unfed.join(', ')}.` : ''
  const sev: TileSev = behind === 0 ? 'good' : atRisk > 0 ? 'critical' : 'warning'
  return {
    key: r.rep, label: r.rep, view: 'rep', measured: true, figure: atRisk, money: true,
    behind, total, severity: sev, unit: 'dollars', needsWork,
    note: behind === 0
      ? `Tier ${Math.round((num(c.tier) || 0) * 100)}% — every measured KPI met.${tail}${where}`
      : `Tier ${Math.round((num(c.tier) || 0) * 100)}%, short on ${behind} of ${total} focused KPI(s).${tail}${where}`,
  }
}

/** THE TILE ROW. `decls` is the TENANT'S declared axis and is required: with none, there are no
 *  tiles. `selected` is the metric picker's narrowing ([] = every declared metric). */
export function buildTiles(stores: PlanStore[], decls: MetricDecl[], view: TileView, selected: string[]): Tile[] {
  const want = focused(decls, selected)
  if (!want.length || !(stores || []).length) return []
  let tiles: Tile[]
  if (view === 'kpi') {
    tiles = want
      .map(d => (d.grain === 'store' ? storeMetricTile(stores, d) : kpiTile(stores, d)))
      .filter((t): t is Tile => t !== null)
  } else if (view === 'store') {
    tiles = (stores || []).map(s => storeTile(s, want))
  } else {
    tiles = repsFolded(stores).map(r => repTile(r, want))
  }
  return tiles.sort((a, b) =>
    (SEV_RANK[a.severity] - SEV_RANK[b.severity]) ||
    (b.behind - a.behind) ||
    ((b.money ? (b.figure || 0) : 0) - (a.money ? (a.figure || 0) : 0)) ||
    a.label.localeCompare(b.label))
}

// ── THE STANDARD FILTERS, over the rows the page already loaded ──────────────────────────────────
/** True for the peer-gap item, which compares STORES on the same footfall and names no colleague.
 *  `peer_comparison.PEER_METRIC_LABEL` is the backend's own label and travels on the item. */
const isPeerItem = (it: PlanItem) => (it.metric || '').toLowerCase().includes('peer')

/** The plan, narrowed by the shared filter bar's selection.
 *
 *  Store and market are the shared predicate's job (`matchesStandardFilter`, which folds case and is
 *  the one home for "does this row match the picker"). The rep half cannot be: a plan row is a STORE
 *  carrying many reps, so a store matches when any of its reps does, and the rep list inside it is
 *  narrowed to the people picked.
 *
 *  A REP PICKED WITHOUT A STORE drops the store-wide items, exactly as this surface has always done:
 *  a store-wide figure is a cross-rep figure, and it tells one rep what the rest of the shop is
 *  leaving on the table. The peer-gap item survives, because it names a store and never a person. */
export function filterPlan(stores: PlanStore[], sel: StandardFilterValue): PlanStore[] {
  const reps = (sel?.reps || [])
  const crossRep = reps.length > 0 && !(sel?.stores || []).length
  const out: PlanStore[] = []
  for (const s of stores || []) {
    if (!matchesStandardFilter(s, { ...sel, reps: [] }, { store: x => storeName(x), market: x => x.market })) continue
    const kept = reps.length
      ? (s.reps || []).filter(r => matchesStandardFilter(r, { ...sel, stores: [], markets: [] }, { rep: x => x.rep }))
      : (s.reps || [])
    if (reps.length && !kept.length) continue
    out.push({
      ...s,
      reps: kept,
      items: crossRep ? (s.items || []).filter(isPeerItem) : (s.items || []),
    })
  }
  return out
}

// ── SENDING THE TILE ROW ─────────────────────────────────────────────────────────────────────────
/** The tiles as the shared snapshot sheet takes them (`lib/tile-snapshot.tileSnapshotSheet`) — no
 *  second mailed shape. `measured` is false on every row because THIS REPORT COMPARES NO TWO
 *  PERIODS: there is no change to state, and a percent there would be invented. Whether the tile's
 *  own figure was measured lives in the note, which is the only small print a mailed cell has. */
export function tilesToSnapshot(tiles: Tile[]): SnapshotTile[] {
  return (tiles || []).map(t => ({
    label: t.label,
    value: !t.measured ? null : t.money ? t.figure : `${t.behind} of ${t.total} behind`,
    previous: null,
    changePct: null,
    measured: false,
    money: t.money,
    note: t.note,
  }))
}

/** THE DETAIL, NARROWED TO THE FOCUSED METRICS — owner, 2026-11-12: "if one metric is slected from
 *  the drop down it shoudl only hsow that metrics across all stores and all reps as filtered or all
 *  reps and all stores if not filtered."
 *
 *  It lives here, beside `focused` and `buildTiles`, and reuses the SAME predicate: two ideas of
 *  what "focused" means is how a tile row and the detail under it come to disagree.
 *
 *  What it narrows and what it deliberately does not:
 *  - Store metric rows, store items and rep items are kept only where they are ABOUT a focused
 *    metric, matched on the metric's own grain (`store:` vs `kpi:`) so a target category and a KPI
 *    sharing a word cannot pick each other up.
 *  - A rep's scored KPIs and the unmeasured ones are narrowed the same way, so focusing one KPI
 *    shows that KPI's state rather than all of them.
 *  - EVERY store and EVERY rep is kept. Focusing a metric is not a store filter: a store carrying
 *    nothing about the focused metric must still appear, or its absence of a figure becomes an
 *    absence of the store.
 *  - The at-risk MONEY is never narrowed. A tier is earned across all of a rep's KPIs, so a payout
 *    attributed to one of them would be a figure nobody computed.
 *
 *  An empty selection means "every declared metric" and returns the rows untouched. PURE. */
export function focusPlan(stores: PlanStore[], selected: string[]): PlanStore[] {
  const rows = stores || []
  if (!selected || selected.length === 0) return rows
  const want = new Set(selected)
  const wantsStore = (key: string) => want.has(metricKey('store', key))
  const wantsKpi = (key: string) => want.has(metricKey('kpi', key))
  // A store item names a target category; a rep item names either a KPI or a store category it is
  // failing against (the conversion item names `conversion`). Either grain being focused keeps it.
  const wantsEither = (key: string) => wantsStore(key) || wantsKpi(key)
  return rows.map(s => {
    const reps = (s.reps || []).map(r => {
      const c = r.commission
      return {
        ...r,
        items: (r.items || []).filter(it => wantsEither(it.metric)),
        commission: c ? {
          ...c,
          kpis: (c.kpis || []).filter(k => wantsKpi(k.kpi)),
          kpis_no_data: (c.kpis_no_data || []).filter(k => wantsKpi(k.kpi)),
          short_kpis: (c.kpis || []).filter(k => wantsKpi(k.kpi) && !k.met).map(k => k.label),
        } : c,
      }
    })
    return {
      ...s,
      metrics: (s.metrics || []).filter(m => wantsStore(m.cat)),
      items: (s.items || []).filter(it => wantsEither(it.metric)),
      reps,
    }
  })
}
