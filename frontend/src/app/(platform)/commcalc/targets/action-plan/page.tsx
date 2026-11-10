'use client'
// Daily Action Plan — TILED (owner ask 2026-11-10, verbatim: *"Need to organize the daily action
// plan to a more presentable way, it is right now a long report, collapse it per store, give our
// standard filters for store rep market date range, which metrics needs to be improved / all /
// family plan etc. Make it tiled with focused metrics — cells on the all line metrics and it shows
// who needs improvement and so forth with the details at the bottom, the tiles should be switched
// between kpi / stores / sales reps"*).
//
// WHAT CHANGED AND WHAT DID NOT. The payload is the SAME `GET /commcalc/targets/{period}/action-plan`
// it always was: no new endpoint, no second derivation, not one figure re-folded here. Every verdict
// on screen was reached on the server (`targets_engine.build_action_items` for a category's
// severity, `kpi_failing.evaluate` for a KPI's pass/fail and for what nothing fed, the calculated
// commission rows for at-risk money, `targets_drilldown.team_totals` for the team roll-up). This
// file arranges them; `@/lib/action-plan-tiles` is the ONE home for how they are grouped, and it is
// unit-proved away from the DOM.
//
// THE FOUR THINGS THE REDESIGN ADDS
//   1. A TILE ROW of focused metrics, switchable between KPI / Stores / Sales reps. A tile's
//      headline is the COUNT of who needs improvement — the cardinality of the list it prints — so
//      it stays true under every narrowing the filters apply. Nothing is re-summed here.
//   2. A METRIC PICKER over the metrics THIS TENANT declared (read off the payload, never a built-in
//      list — directive 2026-10-30). Nothing declared means no tiles, never house words.
//   3. THE STANDARD FILTERS (month · stores · markets · people) through the shared
//      <StandardFilterBar>, plus the "as of" day, which is a real server parameter (`today=`) and
//      not a client narrowing. A month is the grain the targets engine spreads a target over, so the
//      period control is the month picker rather than a free range — a range across two months has
//      no single target to be behind on, and inventing one would be a fabricated denominator.
//   4. THE DETAIL COLLAPSED PER STORE at the bottom, closed by default.
//
// NOT MEASURED IS NOT ZERO. A tile whose metric nothing fed says so and carries no figure; "0 behind"
// would read as a clean sheet. Live today: the DLAR gross adds / activations / upgrades columns stop
// arriving after the Elevate Go renames, so the KPIs over them land in `commission.kpis_no_data`.
//
// ── WHERE EACH FIGURE CAME FROM (§CORE-17.3m) ────────────────────────────────────────────────────
// Two grains reach this screen through the shared drill renderer. "Commission at risk" opens to the
// REPS behind it, out of the calculated-commission rows — and a rep whose subtotal the calculation
// never worked out is shown WITHOUT a figure rather than as $0.00 at risk. "Your team total" opens
// to the STORES in the plan: what each achieved, what was assigned to it, and the per-store gap that
// names both. That total is folded ONCE on the server (`team_total`) and read here.
//
// NO DRILL for the count tiles — critical items, warnings and stores with targets are the
// cardinality of the cards printed below them, so the list IS what they count. The same is true of
// every focus tile: its figure is the length of the list inside it.
import { useEffect, useMemo, useState } from 'react'
import { api, ORG_ID, localToday } from '@/lib/client'
import { FixItNotice, useFixGuide } from '@/lib/fix-it'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { tileSnapshotSheet } from '@/lib/tile-snapshot'
import { targetState } from '@/lib/target-state'
import DateField from '@/components/DateField'
import EntityPicker from '@/components/EntityPicker'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import {
  buildTiles, filterPlan, metricText, planMetrics, tilesToSnapshot, TILE_VIEWS,
  type PlanItem, type PlanStore, type Tile, type TileSev, type TileView,
} from '@/lib/action-plan-tiles'

const dtext = (r: DrillRow, field: string) => {
  const v = r.fields?.[field]
  return v == null || v === '' ? '' : String(v)
}
const PLAN_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Store', get: r => dtext(r, 'store') || '—', sortValue: r => dtext(r, 'store') },
  { header: 'Market', get: r => dtext(r, 'market') || '—', sortValue: r => dtext(r, 'market') },
  { header: 'Tier', get: r => dtext(r, 'tier') || '—', sortValue: r => Number(r.fields?.tier ?? -1) },
  { header: 'KPIs met', get: r => dtext(r, 'kpis met') || '—', sortValue: r => Number(r.fields?.['kpis met'] ?? -1) },
]

interface PeerMeta {
  stores_compared?: number; bands?: number; lagging?: number; unbanded?: number
  gap_metric?: string; items_shown?: number; lagging_not_planned?: string[]
  caveats?: { column: string; severity: string; message: string }[]
  note?: string; error?: string
}
/** One category of the team roll-up as the server folded it (§CORE-17.3m). */
interface TeamCat { monthly?: number | null; achieved_mtd?: number | null; need?: number | null }
interface Resp {
  period: string; today: string
  summary: { critical: number; warning: number; stores: number; commission_at_risk: number }
  setup_hint?: string
  peer?: PeerMeta | null
  /** §CORE-17.3m — the team total, folded ONCE on the server over the plan's own store rows. */
  team_total?: { categories?: Record<string, TeamCat> }
  /** §CORE-17.3m — `team_<figure>_<category>` and `commission_at_risk`. */
  drilldown?: Record<string, DrillPanel>
  stores: PlanStore[]
}

const SEV = {
  critical: { bg: 'var(--c-bg-fef2f2)', border: 'var(--c-bg-fecaca)', fg: 'var(--c-fg-b91c1c)', icon: '🔴', label: 'Critical' },
  warning: { bg: 'var(--c-bg-fffbeb)', border: 'var(--c-bg-fde68a)', fg: 'var(--c-fg-92400e)', icon: '🟠', label: 'Warning' },
  good: { bg: 'var(--c-bg-f0fdf4)', border: 'var(--c-bg-bbf7d0)', fg: 'var(--c-fg-15803d)', icon: '🟢', label: 'On track' },
} as const
/** A tile's accent. `none` is the ABSENCE of a verdict (nothing fed it) — grey, never green. */
const TILE_ACCENT: Record<TileSev, string> = {
  critical: 'var(--c-fg-b91c1c)', warning: 'var(--c-fg-92400e)',
  good: 'var(--green)', none: 'var(--border)',
}

function tabLink(active: boolean): React.CSSProperties {
  return {
    padding: '7px 16px', borderRadius: 9, fontSize: 13, fontWeight: 600, textDecoration: 'none',
    background: active ? 'var(--accent)' : 'var(--surface2)',
    color: active ? 'var(--c-fg-ffffff)' : 'var(--text2)', border: '1px solid var(--border)',
  }
}
function toggleBtn(active: boolean): React.CSSProperties {
  return {
    padding: '6px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    background: active ? 'var(--accent)' : 'var(--surface)',
    color: active ? 'var(--c-fg-ffffff)' : 'var(--text2)', border: '1px solid var(--border)',
  }
}

function ItemRow({ it }: { it: PlanItem }) {
  const c = SEV[it.severity]
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 12px', background: c.bg, border: `1px solid ${c.border}`, borderRadius: 8, marginBottom: 6 }}>
      <span style={{ fontSize: 12 }}>{c.icon}</span>
      <div>
        <span style={{ fontWeight: 700, fontSize: 13, color: c.fg }}>{it.title}</span>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}> — {it.detail}</span>
      </div>
    </div>
  )
}

/** ONE FOCUS TILE. The figure is the length of the list beneath it; an unmeasured tile shows a dash
 *  and its reason, because a 0 here is the shape that hides an empty feed. */
function FocusTile({ tile, onPick }: { tile: Tile; onPick: () => void }) {
  const accent = TILE_ACCENT[tile.severity]
  const shown = tile.needsWork.slice(0, 3)
  return (
    <button type="button" onClick={onPick} className="card"
      style={{ padding: '12px 14px', borderLeft: `4px solid ${accent}`, minWidth: 236, flex: '1 1 236px',
               textAlign: 'left', font: 'inherit', cursor: 'pointer' }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{tile.label}</div>
      {tile.measured ? (
        <div style={{ fontSize: 24, fontWeight: 800, color: accent }}>
          {tile.money ? metricText(tile.figure, 'dollars') : tile.behind}
          {!tile.money && <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--text3)' }}> of {tile.total} behind</span>}
        </div>
      ) : (
        <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text3)' }}>not measured</div>
      )}
      <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 2 }}>{tile.note}</div>
      {shown.length > 0 && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 3 }}>
          {shown.map(w => (
            <div key={w.key} style={{ fontSize: 12, color: 'var(--text2)' }}>
              <span style={{ color: SEV[w.severity].fg, fontWeight: 700 }}>·</span> {w.name}
              <span style={{ color: 'var(--text3)' }}> — {w.detail}</span>
            </div>
          ))}
          {tile.needsWork.length > shown.length && (
            <div style={{ fontSize: 11, color: 'var(--text3)' }}>+{tile.needsWork.length - shown.length} more below</div>
          )}
        </div>
      )}
    </button>
  )
}

export default function ActionPlanPage() {
  const { period, setPeriod, periods } = usePeriod()
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [hideOnTrack, setHideOnTrack] = useState(true)
  const [asOf, setAsOf] = useState(localToday())
  const [view, setView] = useState<TileView>('kpi')
  const [focus, setFocus] = useState<string[]>([])            // [] = every declared metric
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const targetFix = useFixGuide('store_targets', 'Action plan')
  const drill = useDrillTile((data?.drilldown as Record<string, DrillPanel>) || {})

  // THE MONTH IS THE SERVER'S QUESTION, so it is NOT held in this page's filter state: the shared
  // period context owns it (the same month every other targets screen is on) and the fetch keys off
  // it. The bar is handed a value DERIVED from the two, which is why there is no effect here
  // copying one into the other — a copy is a second home for the month, and it would disagree with
  // the context the moment another screen moved it.
  // The "as of" day is the engine's own `today=` — pace, what is left and today's share all move
  // with it — so it is a parameter, never a client filter pretending to be one.

  // State writes live in the request's own callbacks (not in the effect body) so a re-render cannot
  // be queued from inside the effect; `alive` drops the answer to a month nobody is looking at.
  useEffect(() => {
    let alive = true
    const url = `/api/v1/commcalc/targets/${encodeURIComponent(period)}/action-plan`
      + `?org_id=${ORG_ID}&today=${encodeURIComponent(asOf || localToday())}`
    api(url).then((d: Resp) => {
      if (!alive) return
      setData(d)
      setLoadError('')
      setLoading(false)
    }).catch((e: unknown) => {
      if (!alive) return
      setLoadError(e instanceof Error ? e.message : String(e))
      setLoading(false)
    })
    return () => { alive = false }
  }, [period, asOf])

  const allStores = useMemo<PlanStore[]>(() => data?.stores || [], [data])
  // THE TENANT'S OWN AXIS — the picker's options are what the endpoint declared for this org.
  const decls = useMemo(() => planMetrics(allStores), [allStores])
  const stores = useMemo(() => filterPlan(allStores, filt), [allStores, filt])
  const tiles = useMemo(() => buildTiles(stores, decls, view, focus), [stores, decls, view, focus])

  // Option sources come from the rows the page already loaded, so a pick always matches a row: the
  // store options and the store cells are the same string, which is the mismatch class that made a
  // store filter silently match nothing elsewhere (§COMMCALC-25).
  const label = (s: PlanStore) => (s.address || s.store_code || '').trim() || s.store_code
  const opts = useMemo(() => optionsFromRows(allStores, { store: label, market: s => s.market }), [allStores])
  const cascade = useMemo(
    () => allStores.map(s => ({ id: label(s), label: label(s), market: (s.market || '').trim() })),
    [allStores])
  const repOptions = useMemo(() => {
    const names = new Set<string>()
    for (const s of allStores) for (const r of s.reps || []) if (r.rep) names.add(r.rep)
    return [...names].sort().map(n => ({ id: n, label: n }))
  }, [allStores])

  const detail = useMemo(() => {
    const keep = (it: PlanItem) => !hideOnTrack || it.severity !== 'good'
    return stores.map(s => ({
      ...s,
      items: (s.items || []).filter(keep),
      reps: (s.reps || []).map(r => ({ ...r, items: (r.items || []).filter(keep) })),
    }))
  }, [stores, hideOnTrack])
  const barValue = useMemo(() => ({ ...filt, period }), [filt, period])

  function buildPayload(): ExportPayload {
    const rows: { scope: string; store: string; rep: string; severity: string; focus: string; detail: string }[] = []
    for (const s of detail) {
      const name = label(s)
      for (const it of s.items) rows.push({ scope: 'Store', store: name, rep: '', severity: it.severity, focus: it.title, detail: it.detail })
      for (const rp of s.reps) for (const it of rp.items) rows.push({ scope: 'Rep', store: name, rep: rp.rep, severity: it.severity, focus: it.title, detail: it.detail })
    }
    return {
      title: 'Daily Action Plan',
      subtitle: `${period} · as of ${data?.today || ''} · ${view} tiles`,
      filename: `Action-Plan-${period.replace(/\s+/g, '-')}`,
      sheets: [
        // THE TILE ROW, through the shared converter — no second mailed shape (§COMMCALC-19).
        tileSnapshotSheet(tilesToSnapshot(tiles), 'Focus tiles'),
        {
          name: 'Action Items',
          columns: [
            { header: 'Scope', get: (r: typeof rows[number]) => r.scope },
            { header: 'Store', get: (r: typeof rows[number]) => r.store },
            { header: 'Rep', get: (r: typeof rows[number]) => r.rep },
            { header: 'Severity', get: (r: typeof rows[number]) => r.severity },
            { header: 'Focus', get: (r: typeof rows[number]) => r.focus },
            { header: 'Detail', get: (r: typeof rows[number]) => r.detail },
          ],
          rows,
        },
      ],
    }
  }

  /** A tile click opens what it is about: a store tile opens that store's detail, and any other tile
   *  focuses that metric so every view narrows to the thing being worked on. */
  function pickTile(t: Tile) {
    if (t.view === 'store') {
      setOpen(o => ({ ...o, [t.key]: !o[t.key] }))
      return
    }
    setFocus(f => (f.length === 1 && f[0] === t.key ? [] : [t.key]))
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
        <a href="/commcalc/targets" style={tabLink(false)}>Daily Targets</a>
        <a href="/commcalc/targets/action-plan" style={tabLink(true)}>Action Plan</a>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Daily Action Plan</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · who needs improvement, by {view === 'kpi' ? 'metric' : view === 'store' ? 'store' : 'sales rep'}.
            Same engine as Daily Targets; the detail for every store is at the bottom.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <ExportButtons payload={buildPayload} compact />
          <SendReportButton reportKey="action_plan" exportPayload={buildPayload} compact
            title="Daily Action Plan"
            filters={{ period, as_of: asOf, view, metrics: focus, ...(filt.stores.length ? { stores: filt.stores } : {}),
                       ...(filt.markets.length ? { markets: filt.markets } : {}), ...(filt.reps.length ? { reps: filt.reps } : {}) }} />
        </div>
      </div>

      {/* THE STANDARD FILTERS (§CORE-27), with the month driving the server read. */}
      <StandardFilterBar
        value={barValue}
        onChange={v => { if (v.period && v.period !== period) setPeriod(v.period); setFilt({ ...v, period: '' }) }}
        periodMode="month"
        periods={periods}
        cascadeStores={cascade}
        storeOptions={opts.stores}
        marketOptions={opts.markets}
        repOptions={repOptions}
        right={
          <>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              As of
              <DateField value={asOf} onChange={e => setAsOf(e.target.value)} aria-label="As of date"
                style={{ padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }} />
            </label>
            {/* WHICH METRICS NEED IMPROVING — this tenant's own declared axis, nothing else. */}
            <EntityPicker multi options={decls.map(d => ({ id: d.key, label: d.label }))}
              value={focus} onChange={setFocus} width={220}
              placeholder={decls.length ? 'All metrics' : 'No metrics declared'}
              ariaLabel="Focus metrics" />
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <input type="checkbox" checked={hideOnTrack} onChange={e => setHideOnTrack(e.target.checked)} /> Hide on-track
            </label>
          </>
        }
      />

      {/* THE TOGGLE — one tile row, three ways of asking the same question. */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Tiles by</span>
        {TILE_VIEWS.map(v => (
          <button key={v.key} type="button" style={toggleBtn(view === v.key)} onClick={() => setView(v.key)}
            aria-pressed={view === v.key}>{v.label}</button>
        ))}
        {focus.length > 0 && (
          <button type="button" style={toggleBtn(false)} onClick={() => setFocus([])}>
            Focused on {focus.length} metric{focus.length === 1 ? '' : 's'} · show all
          </button>
        )}
      </div>

      {loadError && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 16, borderLeft: '4px solid var(--c-bg-92400e)' }}>
          <div style={{ fontSize: 13, color: 'var(--c-fg-92400e)' }}>The plan could not be loaded: {loadError}</div>
        </div>
      )}

      {/* THE FOCUS TILES. Nothing declared means nothing identified, and this says so rather than
          falling back to another tenant's words (directive 2026-10-30). */}
      {!loading && tiles.length > 0 && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          {tiles.map(t => <FocusTile key={t.key} tile={t} onPick={() => pickTile(t)} />)}
        </div>
      )}
      {!loading && data && tiles.length === 0 && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 18 }}>
          <div style={{ fontSize: 13, color: 'var(--text2)' }}>
            {decls.length === 0
              ? 'No metric is declared for this organisation in this period, so there is nothing to tile. Declare the categories you sell on Count Fields and set monthly targets in Target Settings.'
              : focus.length > 0
                ? 'None of the focused metrics is carried by the stores in view. Clear the focus or widen the filters.'
                : 'No store in view carries a declared metric for this period.'}
          </div>
        </div>
      )}

      {data && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
          <div className="card" style={{ padding: '12px 18px', borderLeft: `4px solid ${SEV.critical.fg}` }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: SEV.critical.fg }}>{data.summary.critical}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>critical items</div>
          </div>
          <div className="card" style={{ padding: '12px 18px', borderLeft: `4px solid ${SEV.warning.fg}` }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: SEV.warning.fg }}>{data.summary.warning}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>warnings</div>
          </div>
          <div className="card" style={{ padding: '12px 18px' }}>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{data.summary.stores}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>stores with targets</div>
          </div>
          <div className="card" style={{ padding: '12px 18px', borderLeft: `4px solid ${data.summary.commission_at_risk > 0 ? 'var(--c-bg-b45309)' : 'var(--green)'}` }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: data.summary.commission_at_risk > 0 ? 'var(--c-fg-b45309)' : 'var(--green)' }}>{metricText(data.summary.commission_at_risk || 0, 'dollars')}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>commission at risk</div>
            {/* THE REPS BEHIND IT. No link when the endpoint deliberately did not roll this up for
                the signed-in rep — a suppressed total has no rows behind it. */}
            <DrillTileLinks drills={drill.dl('commission_at_risk', 'the reps behind this')} open={drill.open} onToggle={drill.toggle} />
          </div>
        </div>
      )}

      {/* ONE panel open at a time, below the strip — the shared renderer (§CORE-17.3m). */}
      {drill.panel && <div style={{ marginBottom: 18 }}><DrillDownPanel panel={drill.panel} extraColumns={PLAN_DRILL_COLUMNS} /></div>}

      {/* THE PEER COMPARISON'S OWN HONESTY (index §59.7). Three different things a reader would
          otherwise mistake for "nobody is behind their peers": the comparison failed, a lagging
          store has no target so carries no item here, or a column it could not answer. */}
      {data?.peer && (data.peer.error || data.peer.note || (data.peer.caveats?.length ?? 0) > 0) && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 18, borderLeft: '4px solid var(--c-bg-92400e)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--c-fg-92400e)', marginBottom: 6 }}>
            About the peer comparison
          </div>
          {data.peer.note && (
            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>{data.peer.note}</div>
          )}
          {(data.peer.caveats || []).map((c, i) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>{c.message}</div>
          ))}
          {!data.peer.error && (
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
              {data.peer.stores_compared ?? 0} stores compared across {data.peer.bands ?? 0} traffic
              band(s); {data.peer.lagging ?? 0} behind their own band&apos;s median.
            </div>
          )}
        </div>
      )}

      {data?.setup_hint && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 18 }}>
          <div style={{ fontSize: 13 }}>{data.setup_hint}</div>
        </div>
      )}

      {/* THE TEAM TOTAL, FOLDED ONCE ON THE SERVER (§CORE-17.3m) and read here — never reduced in
          the page, which is how one figure became two numbers hoping to agree. */}
      {data && allStores.length > 1 && (() => {
        const labels: Record<string, { label: string; unit: string }> = {}
        for (const s of allStores) for (const m of (s.metrics || [])) {
          if (!labels[m.cat]) labels[m.cat] = { label: m.label, unit: m.unit }
        }
        const served = data.team_total?.categories || {}
        const cats = Object.entries(served)
          .filter(([cat]) => labels[cat])
          .map(([cat, m]) => ({
            cat, label: labels[cat].label, unit: labels[cat].unit,
            target: Number(m?.monthly || 0), achieved: Number(m?.achieved_mtd || 0), need: Number(m?.need || 0),
          }))
        if (!cats.length) return null
        return (
          <div className="card" style={{ padding: '12px 16px', marginBottom: 20 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 8 }}>
              Your team total · {allStores.length} stores under you
              {(filt.stores.length || filt.markets.length || filt.reps.length)
                ? ' · the server folded this over every store in your span, not the narrowed view' : ''}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {cats.map(a => (
                <div key={a.cat} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', minWidth: 120 }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{a.label}</div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{metricText(a.achieved, a.unit)} <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>/ {metricText(a.target, a.unit)}</span></div>
                  <DrillTileLinks
                    drills={drill.dl(`achieved_${a.cat}`, 'stores behind the achieved',
                                     `target_${a.cat}`, 'stores behind the target',
                                     `need_${a.cat}`, 'stores behind what is left')}
                    open={drill.open} onToggle={drill.toggle} />
                  {/* OWNER 2026-09-10: 'target met' used to print for a scope with NO target. The
                      shared verdict decides it — see lib/target-state. */}
                  {(() => { const ts = targetState(a.target, a.need); return (
                    <div style={{ fontSize: 10, color: ts.color }}>{ts.kind === 'short' ? `${metricText(a.need, a.unit)} to go` : ts.label}</div>
                  ) })()}
                </div>
              ))}
            </div>
          </div>
        )
      })()}

      {/* ── THE DETAIL, COLLAPSED PER STORE (the owner's ask) ────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Detail · {detail.length} store{detail.length === 1 ? '' : 's'}
        </div>
        {detail.length > 0 && (
          <div style={{ display: 'flex', gap: 6 }}>
            <button type="button" style={toggleBtn(false)}
              onClick={() => setOpen(Object.fromEntries(detail.map(s => [s.store_code, true])))}>Expand all</button>
            <button type="button" style={toggleBtn(false)} onClick={() => setOpen({})}>Collapse all</button>
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>Loading…</div>
      ) : !data || detail.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          {(filt.stores.length || filt.markets.length || filt.reps.length)
            ? 'No store matches the filters.'
            : hideOnTrack
              ? 'Nothing flagged — every store with targets is on track. 🎉'
              /* The SECOND copy of "No targets set. Add monthly targets in Target Settings." was the
                 duplicate the house rules forbid; both pages render the shared notice (§CORE-39). */
              : <div style={{ display: 'inline-block', textAlign: 'left', maxWidth: 560 }}>
                  <FixItNotice fix={targetFix} />
                </div>}
        </div>
      ) : (
        detail.map(s => {
          const isOpen = !!open[s.store_code]
          const name = label(s)
          return (
            <div key={s.store_code} className="card" style={{ marginBottom: 10, padding: 0, overflow: 'hidden' }}>
              <button type="button" onClick={() => setOpen(o => ({ ...o, [s.store_code]: !o[s.store_code] }))}
                aria-expanded={isOpen}
                style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                         padding: '12px 16px', background: 'var(--surface2)', border: 'none', borderBottom: isOpen ? '1px solid var(--border)' : 'none',
                         flexWrap: 'wrap', gap: 8, cursor: 'pointer', font: 'inherit', textAlign: 'left' }}>
                <div>
                  <span style={{ fontSize: 12, color: 'var(--text3)', marginRight: 8 }}>{isOpen ? '▾' : '▸'}</span>
                  <span style={{ fontWeight: 700, fontSize: 15 }}>{name}</span>
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}> · {s.store_code}{s.market ? ` · ${s.market}` : ''}</span>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  {s.counts.critical > 0 && <span style={{ fontSize: 11, fontWeight: 700, color: SEV.critical.fg, background: SEV.critical.bg, border: `1px solid ${SEV.critical.border}`, borderRadius: 20, padding: '2px 10px' }}>{s.counts.critical} critical</span>}
                  {s.counts.warning > 0 && <span style={{ fontSize: 11, fontWeight: 700, color: SEV.warning.fg, background: SEV.warning.bg, border: `1px solid ${SEV.warning.border}`, borderRadius: 20, padding: '2px 10px' }}>{s.counts.warning} warning</span>}
                  {s.conversion?.billpays > 0 && <span style={{ fontSize: 11, color: 'var(--text2)', alignSelf: 'center' }}>conv {metricText(s.conversion.rate, 'percent')}</span>}
                  <span style={{ fontSize: 11, color: 'var(--text3)', alignSelf: 'center' }}>{s.reps.length} rep{s.reps.length === 1 ? '' : 's'}</span>
                </div>
              </button>
              {isOpen && (
                <div style={{ padding: '12px 16px' }}>
                  {s.metrics?.length > 0 && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
                      {s.metrics.map(m => (
                        <div key={m.cat} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px', minWidth: 118 }}>
                          <div style={{ fontSize: 11, color: 'var(--text3)' }}>{m.label}</div>
                          <div style={{ fontSize: 14, fontWeight: 700 }}>{metricText(m.achieved, m.unit)} <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>/ {metricText(m.target, m.unit)}</span></div>
                          {(() => { const ts = targetState(m.target, m.need); return (
                            <div style={{ fontSize: 10, color: ts.color }}>{ts.kind === 'short' ? `${metricText(m.need, m.unit)} to go · ${metricText(m.pace, m.unit)}/day` : ts.label}</div>
                          ) })()}
                        </div>
                      ))}
                    </div>
                  )}
                  {s.items.length > 0 && (
                    <div style={{ marginBottom: s.reps.length ? 14 : 0 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>Store</div>
                      {s.items.map((it, i) => <ItemRow key={i} it={it} />)}
                    </div>
                  )}
                  {s.reps.map(rp => (
                    <div key={rp.rep} style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                        {rp.rep}{rp.below_store ? ' · below store' : ''}
                      </div>
                      {rp.items.map((it, i) => <ItemRow key={i} it={it} />)}
                      {rp.commission && (
                        <div style={{ margin: '2px 0 4px' }}>
                          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>
                            Commission tier {Math.round(rp.commission.tier * 100)}% · {rp.commission.kpis_met}/{rp.commission.total_kpis} KPIs{rp.commission.at_risk > 0 ? ` · ${metricText(rp.commission.at_risk, 'dollars')} at risk` : ''}
                          </div>
                          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                            {rp.commission.kpis.map(k => (
                              <span key={k.kpi} title={`${k.label}: ${k.actual}% vs ${k.target}% target`}
                                style={{
                                  fontSize: 10, padding: '2px 7px', borderRadius: 12, fontWeight: 600,
                                  background: k.met ? 'var(--c-bg-f0fdf4)' : 'var(--c-bg-fef2f2)', color: k.met ? 'var(--c-fg-15803d)' : 'var(--c-fg-b91c1c)',
                                  border: `1px solid ${k.met ? 'var(--c-bg-bbf7d0)' : 'var(--c-bg-fecaca)'}`,
                                }}>
                                {k.met ? '✓' : '✗'} {k.label} {k.actual}%
                              </span>
                            ))}
                            {/* NOT MEASURED IS NOT A FAILED KPI, and it is not a met one either. */}
                            {(rp.commission.kpis_no_data || []).map(k => (
                              <span key={k.kpi} title={`${k.label}: no recorded value this period (target ${k.target}%)`}
                                style={{
                                  fontSize: 10, padding: '2px 7px', borderRadius: 12, fontWeight: 600,
                                  background: 'var(--surface2)', color: 'var(--text3)', border: '1px solid var(--border)',
                                }}>
                                — {k.label} not measured
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })
      )}
    </div>
  )
}
