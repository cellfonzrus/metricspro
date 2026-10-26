'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { tileSnapshotSheet, type SnapshotTile } from '@/lib/tile-snapshot'
import ReportShell from '@/components/ReportShell'
import { useActiveCarrier } from '@/lib/auth-context'
import { MultiSelect } from '@/lib/multiselect'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import { agreesToTheCent } from '@/lib/drill-narrow'

// Sales Comparison — period-over-period % change per item sold (Phones, BYOD, Accessories, Tablets,
// Financing), across all stores. The backend does the two-window math; this page picks the scenario
// (month-over-month / year-over-year / custom · optional week-of-month · optional "as of day N") and
// renders tiles + a store×category table via ReportShell (filter / group / export / send for free).

// Super-admin org-resolution mitigation, same as the Sales Report (reads carry no org_id in the URL).
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

// A % change → a signed, arrow-prefixed label. null = no base to compare against (a brand-new item).
function pctLabel(pct: number | null, current: number): string {
  if (pct === null || pct === undefined) return current > 0 ? '▲ new' : '—'
  const a = pct > 0 ? '▲' : pct < 0 ? '▼' : '±'
  return `${a} ${pct > 0 ? '+' : ''}${pct}%`
}
// A change the backend says CANNOT be measured is labelled, never drawn as a percent. The expense
// case: when both months inherit the same carried sheet, the figures are one month against itself,
// and a ± 0% there reads as "expenses held steady" (index §COMMCALC-18.7).
function changeLabel(pct: number | null, current: number, measured?: boolean): string {
  return measured === false ? 'not measured' : pctLabel(pct, current)
}
function pctColor(pct: number | null, current: number): string {
  if (pct === null || pct === undefined) return current > 0 ? 'var(--green, #16a34a)' : 'var(--text3)'
  if (pct > 0) return 'var(--green, #16a34a)'
  if (pct < 0) return 'var(--red, #dc2626)'
  return 'var(--text3)'
}

// A per-ITEM comparison tile. HOISTED TO MODULE SCOPE 2026-10-26: it used to be declared inside
// the page body, which makes it a new component TYPE on every render, so React unmounts and
// remounts its whole subtree instead of updating it (`react-hooks/static-components`, owner
// report 2026-10-21 — the keystroke that did not arrive). Wiring the drill links into it put a
// typed control within reach of that remount, which `harness_inner_component_inputs.py` refuses
// outright and may never ratchet. The only page value it closed over was the drill handle, so
// that is now an explicit prop and the hazard is gone rather than stepped around — `OverTile`
// above was hoisted for the same reason.
function Tile({ c, d }: {
  c: any
  /** The page's drill handle — `useDrillTile`'s return, passed in rather than closed over. */
  d: {
    dl: (...a: string[]) => { key: string; label: string }[]
    open: string | null
    toggle: (k: string) => void
  }
}) {
  // Metric-aware per the owner spec: units rows headline the count; Accessories headlines $; Financing
  // shows BOTH the financed-unit count and the financed $.
  const metric: string = c.metric || 'units'
  const isDollars = metric === 'dollars'
  const isBoth = metric === 'both'
  const bigVal = isDollars ? fmt(c.current_rev || 0) : c.current
  const bigPct = isDollars ? c.rev_pct : c.pct
  const bigCur = isDollars ? (c.current_rev || 0) : c.current
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600, display: 'flex', gap: 6, alignItems: 'center' }}>
        {c.label}
        {c.financing && c.detection_status && c.detection_status !== 'configured' &&
          <span title="Financing detection is not fully mapped — see the Financing report settings" style={{ color: 'var(--c-fg-b45309)' }}>⚠︎</span>}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 3 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{bigVal}</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: pctColor(bigPct, bigCur) }}>{pctLabel(bigPct, bigCur)}</div>
      </div>
      {isBoth &&
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{fmt(c.current_rev || 0)}</div>
          <div style={{ fontSize: 12, fontWeight: 700, color: pctColor(c.rev_pct, c.current_rev || 0) }}>{pctLabel(c.rev_pct, c.current_rev || 0)}</div>
        </div>}
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
        {isDollars
          ? <>was {fmt(c.previous_rev || 0)} · {c.current} units</>
          : isBoth
            ? <>was {c.previous} units · {fmt(c.previous_rev || 0)}</>
            : <>was {c.previous} · {fmt(c.current_rev || 0)}</>}
      </div>
      {/* One link per FIGURE, never one per tile: "which rows is this?" is a different question
          for the units and the dollars of each of the two windows. */}
      <DrillTileLinks
        drills={d.dl(
          `item.${c.key}.current`, 'units, this window',
          `item.${c.key}.current_rev`, '$, this window',
          `item.${c.key}.previous`, 'units, compared',
          `item.${c.key}.previous_rev`, '$, compared')}
        open={d.open} onToggle={d.toggle} />
    </div>
  )
}


// An overall comparison tile. It closes over nothing, so it lives at MODULE scope — a component
// defined inside the page body is re-created on every render (react-hooks/static-components).
// `invert` is for a COST: spending more is not the green that selling more is, so the colour of the
// change is flipped while the number and the sign are untouched.
function OverTile({ label, cur, prev, pct, money, invert, measured, drills, open, onToggle }: {
  label: string; cur: number; prev: number; pct: number | null; money?: boolean; invert?: boolean; measured?: boolean
  /** One link per FIGURE this tile shows — this window's and the comparison window's. The change
   *  between them gets none: it is taken at the TOTAL and both sides drill (§CORE-17.3k). */
  drills?: { key: string; label: string }[]
  open?: string | null
  onToggle?: (k: string) => void
}) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150, background: 'var(--surface2)' }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 3 }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{money ? fmt(cur) : cur}</div>
        <div style={{ fontSize: 13, fontWeight: 700, color: measured === false ? 'var(--text3)' : pctColor(invert && pct !== null && pct !== undefined ? -pct : pct, cur) }}>
          {changeLabel(pct, cur, measured)}
        </div>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>was {money ? fmt(prev) : prev}</div>
      {drills && drills.length > 0 && onToggle &&
        <DrillTileLinks drills={drills} open={open ?? null} onToggle={onToggle} />}
    </div>
  )
}

// WHERE EACH TILE CAME FROM (index §CORE-17.3r). Declaration:
// commcalc/sales_margin_drilldown.py, folded over the SAME store-and-item rows, the SAME per-store
// summary and the SAME category totals this endpoint serves.
//
// THE TWO WINDOWS OPEN SEPARATELY, and that is not a cosmetic split: a comparison window is a
// DIFFERENT PERIOD, so the sales read may have answered out of different tables for it, and each
// window's provenance is resolved off its OWN read's meta (§CORE-17.3n). Seeing which read
// answered for which month is most of what a reader comparing two months needs.
//
// NOT NARROWED, AND THE LOCK FAILS THE BUILD IF `narrowPanel` APPEARS HERE
// (harness_batch18_drilldown.py §F). The store and market pickers here are QUERY PARAMETERS
// (`qs.set('stores', ...)`), so the server folds again and every tile follows by construction;
// this page reduces nothing.
//
// WHAT OFFERS NO DRILL: every `Change` and every `Change %`. The subtraction is taken at the TOTAL
// from two independent window folds (§CORE-17.3k) and the percentage is its quotient
// (§CORE-17.3h) — and BOTH SIDES of each one drill, which is the whole of the answer. The expense
// line's unit columns get none either: the producer pins them at 0 because an expense has no unit,
// and a panel over them would be a zero that ties against nothing.
const CMP_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Market', get: (r: DrillRow) => String(r.fields?.['market'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['market'] ?? '') },
]

const MODES = [
  { key: 'mom', label: 'Month over month' },
  { key: 'yoy', label: 'Year over year' },
  { key: 'custom', label: 'Custom period' },
]
const WEEKS = [
  { v: 0, label: 'Whole month' },
  { v: 1, label: 'Week 1 (1–7)' },
  { v: 2, label: 'Week 2 (8–14)' },
  { v: 3, label: 'Week 3 (15–21)' },
  { v: 4, label: 'Week 4 (22–28)' },
  { v: 5, label: 'Week 5 (29–31)' },
]

export default function SalesComparisonPage() {
  // Active-carrier lens (compliance-critical). The Financing line is ONE carrier-scoped row: the active
  // carrier's code is sent to the backend, which returns ONLY that carrier's financing — the report can
  // never show both Boost's and Total's financing vendors together. The vendor brand (ACIMA/TW/Edge) is
  // never named here or in the payload.
  const { activeCarrier } = useActiveCarrier()
  const [period, setPeriod] = useState(thisMonth())
  const [mode, setMode] = useState('mom')
  const [comparePeriod, setComparePeriod] = useState('')   // custom mode only
  const [week, setWeek] = useState(0)
  const [asOf, setAsOf] = useState<string>('')             // '' = auto, else a day number, '0' = full month
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period, mode, week: String(week) })
    if (mode === 'custom' && comparePeriod) qs.set('compare_period', comparePeriod)
    if (asOf !== '') qs.set('as_of_day', asOf)   // '' → backend auto; else explicit (0 = full month)
    if (selStores.length) qs.set('stores', selStores.join(','))
    if (selMarkets.length) qs.set('markets', selMarkets.join(','))
    if (activeCarrier) qs.set('carrier', activeCarrier)   // carrier-scoped Financing line (compliance)
    api(`/api/v1/commcalc/sales-comparison?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, mode, comparePeriod, week, asOf, selStores, selMarkets, activeCarrier])
  useEffect(() => { load() }, [load])

  // Per-ITEM tiles only. Store expenses are a cost, not an item sold, and get their own tile beside
  // Revenue below — never a tile in the row of things the stores sold.
  const cats: any[] = (data?.totals_by_category || []).filter((c: { expense?: boolean }) => !c.expense)
  const rows: any[] = data?.rows || []
  const baseLbl = data?.base_period || period
  const cmpLbl = data?.compare_period || ''

  // Accessories are a DOLLARS item and Store expenses have no unit at all, so a table that changed
  // only on units could not show either one moving month over month (owner ask 2026-10-17). The $
  // change and $ change % sit beside the unit pair, from `rev_delta` / `rev_pct` the payload already
  // carries — the same two figures the tiles headline, so a tile and its rows cannot disagree.
  const cols = useMemo<ExportColumn[]>(() => [
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market || '—' },
    { header: 'Item', get: r => r.category },
    { header: `Units ${baseLbl}`, get: r => r.current, align: 'right' },
    { header: `Units ${cmpLbl}`, get: r => r.previous, align: 'right' },
    { header: 'Change', get: r => (r.delta > 0 ? '+' : '') + r.delta, align: 'right' },
    { header: 'Change %', get: r => pctLabel(r.pct, r.current), align: 'right' },
    { header: `$ ${baseLbl}`, get: r => r.current_rev, money: true },
    { header: `$ ${cmpLbl}`, get: r => r.previous_rev, money: true },
    { header: '$ Change', get: r => r.rev_delta, money: true },
    { header: '$ Change %', get: r => pctLabel(r.rev_pct, r.current_rev), align: 'right' },
  ], [baseLbl, cmpLbl])

  // THE OFFER RULE, ONE HOME: offered only where the server's panel agrees with the tile to the
  // cent, through the shared gate rather than a condition invented here.
  const cmpPanels: Record<string, DrillPanel> = data?.drilldown || {}
  // The figure a panel key belongs to, read out of the SAME two objects the tiles render from —
  // `item.<category>.<field>`, `expenses.<field>` and `overall.<field>`, which is how the server
  // keys them. No second fold: this only looks a number up.
  const cmpFig = (k: string): number | undefined => {
    const parts = k.split('.')
    if (parts[0] === 'overall') {
      return (data?.overall as Record<string, number> | undefined)?.[parts[1]]
    }
    const catKey = parts[0] === 'item' ? parts[1] : parts[0]
    const field = parts[parts.length - 1]
    const cats = (data?.totals_by_category || []) as Record<string, number | string | boolean>[]
    const c = cats.find(x => x.key === catKey)
    return c === undefined ? undefined : Number(c[field])
  }
  const cmpDrill = useDrillTile(cmpPanels, (k, p) => agreesToTheCent(p, cmpFig(k)))

  const storeOpts: string[] = data?.stores || []
  const marketOpts: string[] = data?.markets || []
  const filtered = selStores.length > 0 || selMarkets.length > 0

  // THE SNAPSHOT (owner ask 2026-10-19). Built from the SAME `totals_by_category` / `overall` objects
  // the tiles below render from — not a second read and not a re-derivation — so what lands in
  // someone's inbox cannot say anything the screen does not. `tileSnapshotSheet` is the one home for
  // the shape; this page only says WHICH tiles it has and what each one means.
  const snapshotTiles = useMemo<SnapshotTile[]>(() => {
    const t: SnapshotTile[] = []
    for (const c of (data?.totals_by_category || [])) {
      const dollars = c.metric === 'dollars'
      const both = c.metric === 'both'
      if (c.expense) {
        t.push({
          label: c.label, money: true,
          value: c.current_rev, previous: c.previous_rev,
          changePct: c.rev_pct, measured: c.change_measured !== false,
          note: c.change_measured === false
            ? `Both months carry the same entered sheet, so the change cannot be measured`
            : '',
        })
        continue
      }
      t.push({
        label: c.label, money: dollars,
        value: dollars ? c.current_rev : c.current,
        previous: dollars ? c.previous_rev : c.previous,
        changePct: dollars ? c.rev_pct : c.pct,
        note: dollars ? `${c.current} units`
          : both ? `${fmt(c.current_rev || 0)} financed`
            : c.current_rev ? fmt(c.current_rev) : '',
      })
    }
    if (data?.overall) {
      t.push({ label: 'Transactions', value: data.overall.current_txns, previous: data.overall.previous_txns, changePct: data.overall.txns_pct })
      t.push({ label: 'Revenue', money: true, value: data.overall.current_rev, previous: data.overall.previous_rev, changePct: data.overall.rev_pct })
      if (data?.expenses_meta?.measured) {
        const measured = data.expenses_meta.change_measured !== false
        t.push({
          label: 'Store expenses', money: true,
          value: data.overall.current_exp, previous: data.overall.previous_exp,
          changePct: data.overall.exp_pct, measured,
          note: measured
            ? (data.expenses_meta.base_carried_from ? `${baseLbl} carries ${data.expenses_meta.base_carried_from}` : '')
            : `Both months carry ${data.expenses_meta.base_carried_from || 'the same sheet'}`,
        })
      }
    }
    return t
  }, [data, baseLbl])

  const snapshotPayload = useCallback((): ExportPayload => ({
    title: `Sales Comparison — ${baseLbl} vs ${cmpLbl}`,
    subtitle: `${data?.window_label || ''}${filtered ? ' · filtered' : ' · all stores'}`,
    filename: `sales-comparison-snapshot-${baseLbl}-vs-${cmpLbl}`,
    sheets: [tileSnapshotSheet(snapshotTiles)],
  }), [baseLbl, cmpLbl, data, filtered, snapshotTiles])



  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📈 Sales Comparison</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Period-over-period change per item sold — <b>Phones, BYOD, Activation, Tablets</b> (units),
          <b> Accessories</b> ($) and <b>Financing</b> (units &amp; $) — across all stores. Compare
          month-over-month, year-over-year, or week-1-over-week-1, aligned to the same day of the month.
          Each row carries both the unit change and the <b>$ change</b>, so accessory sales compare on
          dollars. <b>Store expenses</b> are shown per store on the same two months — the same figures the
          P&amp;L books, never added into the revenue beside them.
          The Financing line reflects only the active carrier’s financing.
          <b> 📤 Send snapshot</b> emails these tiles to anyone you pick.
        </p>
      </div>

      {/* scenario controls */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Base month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Compare{' '}
          <select style={sel} value={mode} onChange={e => setMode(e.target.value)}>
            {MODES.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </label>
        {mode === 'custom' &&
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>vs month{' '}
            <input type="month" style={sel} value={comparePeriod} onChange={e => setComparePeriod(e.target.value)} />
          </label>}
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Week{' '}
          <select style={sel} value={week} onChange={e => setWeek(Number(e.target.value))}>
            {WEEKS.map(w => <option key={w.v} value={w.v}>{w.label}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)' }} title="Cut both periods to day ≤ N so a mid-month base compares to the same first-N-days of the other period. Blank = auto (today when the base is the current month).">
          As of day{' '}
          <input type="number" min={0} max={31} placeholder="auto" style={{ ...sel, width: 78 }} value={asOf}
                 onChange={e => setAsOf(e.target.value)} disabled={week > 0} />
        </label>
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        {storeOpts.length > 0 && <MultiSelect allLabel="All stores" width={150} value={selStores} options={storeOpts} onChange={setSelStores} searchable />}
        {filtered && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelStores([]); setSelMarkets([]) }}>Clear filters</button>}
      </div>

      {data && !data.error &&
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>
            Comparing <b>{baseLbl}</b> vs <b>{cmpLbl}</b> · <b>{data.window_label}</b>
            {data?.source_meta && <span style={{ color: 'var(--text3)' }}> · {data.source_meta.base_rows} base / {data.source_meta.compare_rows} compare sale lines</span>}
          </div>
          {/* SEND THE SNAPSHOT (owner ask 2026-10-19). Sending has ONE home — `SendReportButton`
              already owns the recipient picker, the channels, the message and the Notify history —
              so this adds only the CONTENT: the tiles, through `tile-snapshot`'s shared converter.
              It sits HERE, with the tiles it sends; the table's own Send stays on the table. */}
          {snapshotTiles.length > 0 &&
            <SendReportButton
              title={`Sales Comparison snapshot — ${baseLbl} vs ${cmpLbl}`}
              label="📤 Send snapshot"
              exportPayload={snapshotPayload}
              filters={{ period: baseLbl, compare: cmpLbl, window: data?.window_label }}
              compact />}
        </div>}

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: 'var(--c-bg-fee2e2)', color: 'var(--c-fg-991b1b)', fontSize: 13 }}>
          <b>❌ Sales Comparison could not be built.</b> {data.error}
        </div>}

      {/* per-item tiles */}
      {!loading && !data?.error && cats.length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          {cats.map(c => <Tile key={c.key} c={c} d={cmpDrill} />)}
        </div>}

      {/* overall tiles. Store expenses sit HERE, not with the per-item tiles: an expense is not an
          item sold, and it is never added into the revenue beside it. */}
      {!loading && !data?.error && data?.overall &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          <OverTile label="Transactions" cur={data.overall.current_txns} prev={data.overall.previous_txns} pct={data.overall.txns_pct}
                    drills={cmpDrill.dl('overall.current_txns', 'rows behind this', 'overall.previous_txns', 'rows behind the comparison')}
                    open={cmpDrill.open} onToggle={cmpDrill.toggle} />
          <OverTile label="Revenue" cur={data.overall.current_rev} prev={data.overall.previous_rev} pct={data.overall.rev_pct} money
                    drills={cmpDrill.dl('overall.current_rev', 'rows behind this', 'overall.previous_rev', 'rows behind the comparison')}
                    open={cmpDrill.open} onToggle={cmpDrill.toggle} />
          {data?.expenses_meta?.measured &&
            <OverTile label="Store expenses" cur={data.overall.current_exp || 0} prev={data.overall.previous_exp || 0}
                      pct={data.overall.exp_pct} money invert
                      measured={data.expenses_meta.change_measured !== false}
                      drills={cmpDrill.dl('expenses.current_rev', 'rows behind this', 'expenses.previous_rev', 'rows behind the comparison')}
                      open={cmpDrill.open} onToggle={cmpDrill.toggle} />}
        </div>}

      {!loading && !data?.error && cmpDrill.panel &&
        <div style={{ marginBottom: 18 }}>
          <DrillDownPanel panel={cmpDrill.panel} extraColumns={CMP_DRILL_COLUMNS} />
        </div>}

      {/* An expense month the tenant has not re-entered is CARRIED FORWARD — the same rule the P&L
          books. Say which month it came from rather than let the reader assume it was entered. */}
      {!loading && !data?.error && data?.expenses_meta?.measured &&
        (data.expenses_meta.base_carried_from || data.expenses_meta.compare_carried_from) &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12,
                                       color: data.expenses_meta.change_measured === false ? '#92400e' : 'var(--text2)',
                                       background: data.expenses_meta.change_measured === false ? '#fffbeb' : undefined }}>
          {data.expenses_meta.change_measured === false
            ? <>⚠︎ <b>The store-expense change cannot be measured.</b> Neither {baseLbl} nor {cmpLbl} has its own
                entered expenses, so both inherit <b>{data.expenses_meta.base_carried_from}</b> — the two figures are
                that one sheet compared with itself, which is why no percentage is shown. Enter {cmpLbl} and {baseLbl}
                on the Expenses sheet and the comparison becomes real.</>
            : <>ℹ︎ Store expenses carried forward — {baseLbl} {data.expenses_meta.base_carried_from
                ? <>uses <b>{data.expenses_meta.base_carried_from}</b></> : <>has its own entered expenses</>}
              {' · '}{cmpLbl} {data.expenses_meta.compare_carried_from
                ? <>uses <b>{data.expenses_meta.compare_carried_from}</b></> : <>has its own entered expenses</>}.
              {' '}A month with no saved expenses inherits the latest earlier month, exactly as the P&amp;L books it —
              enter that month on the Expenses sheet to compare the real figures.</>}
        </div>}

      {/* SALARY AND COMMISSION COME FROM THE MONTH'S OWN ACTUALS (owner directive 2026-10-20).
          A carried month inherits the rent and not the payroll, so this says where the labour in the
          figure came from — and, when a half could not be measured, says THAT instead of letting a
          missing payroll read as a cheaper month (§19.49). */}
      {!loading && !data?.error && data?.expenses_meta?.measured &&
        (Object.keys(data.expenses_meta.base_excluded || {}).length > 0 ||
         Object.keys(data.expenses_meta.compare_excluded || {}).length > 0 ||
         data.expenses_meta.labour_measured === false) &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12,
                                       color: data.expenses_meta.labour_measured === false ? '#92400e' : 'var(--text2)',
                                       background: data.expenses_meta.labour_measured === false ? '#fffbeb' : undefined }}>
          {data.expenses_meta.labour_measured === false
            ? <>⚠︎ <b>Part of the labour in this figure could not be measured.</b> Salary and commission are taken
                from each month&apos;s own payroll rather than from the carried sheet, and for at least one month here
                the payroll could not be read — so that month&apos;s expense total is INCOMPLETE, not lower. Check the
                month&apos;s time clock and shifts before comparing.</>
            : <>ℹ︎ <b>Salary and commission come from each month&apos;s own actuals.</b> A carried month inherits the
                rent and the utilities, never last month&apos;s payroll — so the labour in these figures is the hours
                and the rep commission recorded for that month itself.
                {data.expenses_meta.base_carried_from && data.expenses_meta.compare_carried_from &&
                 data.expenses_meta.base_carried_from === data.expenses_meta.compare_carried_from &&
                  <> Both months still inherit <b>{data.expenses_meta.base_carried_from}</b> for everything else, so
                    the change shown is the change in <b>labour</b> — enter each month on the Expenses sheet and the
                    rest of the comparison becomes real too.</>}</>}
        </div>}

      {!loading && !data?.error && data && !data.expenses_meta?.measured &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12, color: 'var(--text2)' }}>
          ℹ︎ <b>Store expenses not measured</b> for this selection — the expense sheet could not be read, so the
          expense line is left off rather than shown as $0.00.
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {data?.note || 'No sales in either period for this selection.'}
        </div>
      ) : !data?.error ? (
        /* OPENS COLLAPSED (owner 2026-10-08). Grouped by Store, every store contributes one row PER
           ITEM, so expanded this table is hundreds of rows deep before a manager has picked a store.
           Collapsed, each group header still carries the subtotal for every numeric column, so the
           comparison reads at a glance and a store is one click away. */
        <ReportShell
          title={`Sales Comparison — ${baseLbl} vs ${cmpLbl}`}
          subtitle={`${data?.window_label || ''}${filtered ? ' · filtered' : ' · all stores'} · % change per item sold`}
          filename={`sales-comparison-${baseLbl}-vs-${cmpLbl}`}
          columns={cols}
          rows={rows}
          totals
          stickyHeader
          defaultGroupBy="Store"
          collapsibleGroups
          defaultCollapsed
          groupPersistKey="sales-comparison:groupBy"
        />
      ) : null}

      {!loading && rows.length > 0 &&
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
          💡 Group by <b>Item</b> (top-left of the table) to roll every store up per category, or by <b>Store</b> to see each store’s mix.
        </div>}
    </div>
  )
}
