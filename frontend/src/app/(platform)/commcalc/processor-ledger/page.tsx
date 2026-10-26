'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MarketStorePicker, type StoreOpt } from '@/components/MarketStorePicker'
import { CheckboxDropdown } from '@/components/CheckboxDropdown'
import { NO_MARKET_ID } from '@/lib/market-store-cascade'
import { useReportLabels } from '@/lib/report-labels'
import { sourceLabel } from '@/lib/sourceLabels'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import { narrowPanels, screenKey, agreesToTheCent } from '@/lib/drill-narrow'

// Processor Daily Ledger (owner directive 2026-09-04): the org's carrier-processor money movements
// grouped day × transaction type — DEBITS (money the processor takes/charges) and CREDITS (money it
// pays the dealer) in two columns, NET (credits − debits) in the third. Data: GET
// /commcalc/processor-ledger (org-scoped; store-span gated server-side).
//
// The processor's NAME comes from the mig-953 carrier vocabulary — useReportLabels().term('processor')
// resolves the active carrier's word (tenant override > house carrier preset > the neutral noun
// "payment processor"). This page is shared by BOTH sides and is deliberately NOT carrier-gated in
// NAV_CARRIERS, so it may not contain either side's vendor string; the payload's own resolved label
// (same vocabulary, server-side) backs the term up for exports/scheduled sends.
//
// Filters (client-side over server cells, WYSIWYG): date range, market/store cascade, transaction
// type. Each cell carries its CANONICALLY-resolved market (core.scope, §13a) and the market dropdown
// is fed the canonical §13c option list from the payload — so a market that lives on only one store
// vocabulary is both offered and selectable here. Totals recompute from visible rows so screen,
// export and send always tie out.

interface Cell {
  processor: string; date: string; tx_type: string
  store_code: string; store: string; market: string
  category: string
  debits: number; credits: number; net: number; rows: number
}
interface UnclassifiedType { tx_type: string; debits: number; credits: number; net: number; rows: number }
interface Payload {
  processor: { code: string; label: string; label_source?: string; resolved_from: string }
  market_options: string[]
  category_options: string[]
  categories: {
    declared: string[]; placeable: string[]; map_rows: number
    unclassified_types: UnclassifiedType[]; note: string
  }
  feeds: { processor: string; source: string; rows: number; truncated: boolean; error?: string; classification: string }[]
  cells: Cell[]
  types: string[]
  drilldown?: Record<string, DrillPanel>
  meta: { date_from: string; date_to: string; net_rule: string }
}
interface StoreMeta { store_code: string; store_address: string; market: string }

interface Row { date: string; tx_type: string; category: string; debits: number; credits: number; net: number; rows: number; isDay?: boolean }

// The server's word for "this org declared no category for this payment type"
// (processor_ledger.NO_CATEGORY_ID). A sentinel, not a category: it is appended to the dropdown
// only when a visible cell actually needs it, the same rule §13c keeps for "(no market)".
const NO_CATEGORY_ID = '(not classified)'

// ── WHERE EACH TILE CAME FROM (§CORE-17.3n), AND WHY THIS ONE NARROWS ───────────────────────────
// Debits, Credits and the Net open to the CELLS they were folded from, each row naming the feed
// `processor_ledger.FEED_SHAPES` says it was read out of — so a figure made of two processors'
// movements says which part came from which. Declared in
// commcalc/processor_money_drilldown.py, folded by core/report_drilldown, rendered by the one
// shared panel.
//
// The NET opens like the other two because it is `credits − debits` folded PER CELL and then added
// up: the rows and the figure are reached two different ways, so the tie-out is a real comparison
// (§CORE-17.3k) rather than a restatement of one of them.
//
// WHY IT NARROWS: this page filters cells by market, store, transaction type and category on the
// CLIENT and recomputes its totals over what survives, so the panels are narrowed to the same
// selection (§CORE-17.3d, lib/drill-narrow is the one home). A range whose cells do not all fit in
// one panel is CAPPED server-side, and the one home then refuses to narrow it rather than present a
// shorter figure as the whole of one — so under a filter such a range offers no drill and says so.
//
// "Days · feed rows" gets no panel: two counts whose lists are the table below. Nor do the day ×
// type rows themselves, which re-partition exactly these cells and already print their subtotals.
const dfield = (r: DrillRow, field: string) => {
  const v = r.fields?.[field]
  return v == null || v === '' ? '' : String(v)
}
const CELL_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Store', get: r => dfield(r, 'store'), sortValue: r => dfield(r, 'store') },
  { header: 'Market', get: r => dfield(r, 'market'), sortValue: r => dfield(r, 'market') },
  { header: 'Category', get: r => dfield(r, 'category') || NO_CATEGORY_ID, sortValue: r => dfield(r, 'category') },
  { header: 'Feed rows', get: r => dfield(r, 'feed_rows'), sortValue: r => Number(r.fields?.feed_rows ?? -1) },
]

function firstOfMonth(): string {
  const t = localToday()
  return `${t.slice(0, 8)}01`
}

export default function ProcessorLedgerPage() {
  const { term } = useReportLabels()
  const [dateFrom, setDateFrom] = useState(() => firstOfMonth())
  const [dateTo, setDateTo] = useState(() => localToday())
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [stores, setStores] = useState<StoreMeta[]>([])
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])
  const [fTypes, setFTypes] = useState<string[]>([])
  const [fCats, setFCats] = useState<string[]>([])
  const [showDaySubtotals, setShowDaySubtotals] = useState(true)

  function load() {
    setLoading(true); setErr('')
    const qs = new URLSearchParams({ date_from: dateFrom, date_to: dateTo || dateFrom })
    api(`/api/v1/commcalc/processor-ledger?${qs.toString()}`)
      .then((d: any) => setData(d || null))
      .catch((e: any) => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [dateFrom, dateTo])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP)
      .then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || [])))
      .catch(() => {})
  }, [])

  // The roster supplies the STORE dropdown's labels and the cascade's per-store market. The market
  // a CELL is filtered by comes off the cell itself (resolved server-side through the canonical
  // union) — never re-derived from this roster, so a store the roster is missing keeps its market.
  const storesForCascade: StoreOpt[] = useMemo(
    () => stores.filter(s => s.store_code).map(s => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [stores])
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])
  const fTypesFold = useMemo(() => new Set(fTypes.map(t => t.trim().toLowerCase())), [fTypes])
  const fCatsFold = useMemo(() => new Set(fCats.map(c => c.trim().toLowerCase())), [fCats])

  // Client-side filter over the server cells (WYSIWYG — what survives here is what totals/exports).
  // A market-less cell (an unmapped feed key) matches only the explicit "(no market)" pick — it is
  // never quietly folded into a real market, and never vanishes from the unfiltered view.
  // A category-less cell matches only the explicit "(not classified)" pick — never a real category
  // and never missing from the unfiltered view. Same contract as the market filter above, and the
  // display twin of processor_ledger.filter_cells so screen and deep link agree.
  const cells: Cell[] = useMemo(() => (data?.cells || []).filter(c => {
    const mkt = (c.market || '').trim().toLowerCase()
    const cat = (c.category || '').trim().toLowerCase()
    return (!fStores.length || fStores.includes(c.store_code)) &&
      (!fMarketsFold.size || (mkt ? fMarketsFold.has(mkt) : fMarketsFold.has(NO_MARKET_ID.toLowerCase()))) &&
      (!fTypesFold.size || fTypesFold.has(c.tx_type.trim().toLowerCase())) &&
      (!fCatsFold.size || (cat ? fCatsFold.has(cat) : fCatsFold.has(NO_CATEGORY_ID.toLowerCase())))
  }), [data, fStores, fMarketsFold, fTypesFold, fCatsFold])

  // Day × transaction-type rollup of the visible cells, with per-day subtotal rows.
  const { rows, totals, dayCount } = useMemo(() => {
    const byKey = new Map<string, Row>()
    const byDay = new Map<string, Row>()
    const tot = { debits: 0, credits: 0, net: 0, rows: 0 }
    for (const c of cells) {
      const k = `${c.date} ${c.tx_type}`
      let r = byKey.get(k)
      // `category` rides on the row rather than widening the key: it is a pure function of
      // tx_type, so every cell folded under one (date, tx_type) carries the same one.
      if (!r) { r = { date: c.date, tx_type: c.tx_type, category: c.category || '', debits: 0, credits: 0, net: 0, rows: 0 }; byKey.set(k, r) }
      let d = byDay.get(c.date)
      if (!d) { d = { date: c.date, tx_type: '', category: '', debits: 0, credits: 0, net: 0, rows: 0, isDay: true }; byDay.set(c.date, d) }
      for (const s of [r, d, tot]) { s.debits += c.debits; s.credits += c.credits; s.rows += c.rows }
    }
    const typeRows = [...byKey.values()].sort((a, b) => a.date.localeCompare(b.date) || a.tx_type.localeCompare(b.tx_type))
    const out: Row[] = []
    for (let i = 0; i < typeRows.length; i++) {
      const r = typeRows[i]
      out.push({ ...r, net: r.credits - r.debits })
      const nxt = typeRows[i + 1]
      if (showDaySubtotals && (!nxt || nxt.date !== r.date)) {
        const d = byDay.get(r.date)!
        out.push({ ...d, net: d.credits - d.debits })
      }
    }
    return { rows: out, totals: { ...tot, net: tot.credits - tot.debits }, dayCount: byDay.size }
  }, [cells, showDaySubtotals])

  // THE PANELS, NARROWED TO THE CELLS ON SCREEN — the same selection `cells` is, matched on the row
  // key the server served with every drill row.
  const shownKeys = useMemo(() => new Set(cells.map(c => screenKey({
    processor: c.processor, date: c.date, tx_type: c.tx_type, store_code: c.store_code,
  }))), [cells])
  const narrowed = narrowPanels(data?.drilldown || {}, (_r, k) => shownKeys.has(k))
  // THE OFFER RULE: a drill is offered only where the panel's figure agrees with the tile to the
  // cent — the one home's gate, not a condition this page invented (§CORE-17.3c).
  const tileOf: Record<string, number> = {
    debits: totals.debits, credits: totals.credits, net: totals.net,
  }
  const drill = useDrillTile(narrowed, (key, p) => agreesToTheCent(p, tileOf[key]))
  // ONE FOLD PER FIGURE: the panel's own number where there is one, this page's reduce otherwise.
  const served = (key: string, fallback: number) =>
    agreesToTheCent(narrowed[key], tileOf[key]) ? Number(narrowed[key].figure?.amount ?? fallback) : fallback

  const typeOpts = useMemo(() => (data?.types || []).map(t => ({ id: t, label: t })), [data])
  // §13c: the canonical vocabulary from the endpoint (core.scope.org_market_options), with the
  // "(no market)" sentinel appended AFTER composing — and only when a cell actually needs it.
  // §13c, applied to categories: the canonical list comes from the payload (the org's DECLARED
  // categories ∪ the ones its rows carry), and the "(not classified)" sentinel is appended here —
  // and only when a cell actually has no category, so the pick never appears on a tenant whose
  // every line is mapped.
  const categoryOptions = useMemo(() => {
    const canon = data?.category_options || []
    return (data?.cells || []).some(c => !c.category) ? [...canon, NO_CATEGORY_ID] : canon
  }, [data])
  const catOpts = useMemo(() => categoryOptions.map(c => ({ id: c, label: c })), [categoryOptions])
  const marketOptions = useMemo(() => {
    const canon = data?.market_options || []
    return (data?.cells || []).some(c => !c.market) ? [...canon, NO_MARKET_ID] : canon
  }, [data])
  // The org's word for its processor: the active carrier's vocabulary term, with the payload's
  // server-resolved label (same mig-953 source) and the neutral noun behind it.
  const procLabel = term('processor', data?.processor?.label || 'payment processor')
  const titleCase = procLabel.charAt(0).toUpperCase() + procLabel.slice(1)
  // The unclassified census, computed from the VISIBLE cells so it ties to the table rather than
  // to the unfiltered payload. Reported, never repaired: deciding what an unmapped promo IS moves
  // money between the commission and rebate buckets, so it is the owner's ruling.
  const unclassified = useMemo(() => {
    const byType = new Map<string, { tx_type: string; moved: number; rows: number }>()
    let moved = 0
    for (const c of cells) {
      if (c.category) continue
      const t = byType.get(c.tx_type) || { tx_type: c.tx_type, moved: 0, rows: 0 }
      t.moved += Math.abs(c.debits) + Math.abs(c.credits)
      t.rows += c.rows
      byType.set(c.tx_type, t)
      moved += Math.abs(c.debits) + Math.abs(c.credits)
    }
    return { moved, types: [...byType.values()].sort((a, b) => b.moved - a.moved) }
  }, [cells])
  const feedsWithRows = (data?.feeds || []).filter(f => f.rows > 0)
  const truncated = (data?.feeds || []).some(f => f.truncated)
  const rangeLabel = dateTo && dateTo !== dateFrom ? `${dateFrom} → ${dateTo}` : dateFrom

  function buildPayload(): ExportPayload {
    return {
      title: `${titleCase} Daily Debits & Credits`,
      subtitle: `${rangeLabel} · ${dayCount} day(s) · net ${fmt(totals.net)}`,
      filename: `processor-ledger_${dateFrom}${dateTo && dateTo !== dateFrom ? `_${dateTo}` : ''}`,
      sheets: [{ name: 'Daily ledger', rows, columns: [
        { header: 'Date', get: (r: Row) => r.date },
        { header: 'Transaction type', get: (r: Row) => r.isDay ? `${r.date} TOTAL` : r.tx_type },
        { header: 'Category', get: (r: Row) => r.isDay ? '' : (r.category || NO_CATEGORY_ID) },
        { header: 'Debits', get: (r: Row) => r.debits, money: true },
        { header: 'Credits', get: (r: Row) => r.credits, money: true },
        { header: 'Net', get: (r: Row) => r.net, money: true },
        { header: 'Rows', get: (r: Row) => r.rows, align: 'right' },
      ] as ExportColumn[] }],
    }
  }

  const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const num: React.CSSProperties = { padding: '9px 12px', textAlign: 'right', fontSize: 13, whiteSpace: 'nowrap' }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📒 {titleCase} Daily Debits &amp; Credits</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 780 }}>
            Every money movement your {procLabel} made on your account, by day and <strong>transaction
            type</strong>: <strong>debits</strong> (money taken or charged), <strong>credits</strong> (money paid to
            you), and the <strong>net</strong> per line. Same-day activity groups together so you can see what was
            debited and credited on each date. The <strong>Category</strong> column is your own
            mapping of each payment type — commission, rebate, or whatever your categories are
            called — so you can filter to one kind of money instead of reading transaction names.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 4 }}>From
            <input style={inp} type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} /></label>
          <label style={{ fontSize: 12, color: 'var(--text3)', display: 'flex', alignItems: 'center', gap: 4 }}>To
            <input style={inp} type="date" value={dateTo} min={dateFrom} onChange={e => setDateTo(e.target.value)} /></label>
          {rows.length > 0 && <ExportButtons payload={buildPayload} compact />}
          {rows.length > 0 && <SendReportButton exportPayload={buildPayload} title={`${titleCase} Daily Debits & Credits`} compact />}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 16 }}>
        <Stat label="Debits (charged)" value={fmt(served('debits', totals.debits))} color="var(--c-fg-dc2626)"
          drill={<DrillTileLinks drills={drill.dl('debits', 'movements behind this')} open={drill.open} onToggle={drill.toggle} />} />
        <Stat label="Credits (paid to you)" value={fmt(served('credits', totals.credits))} color="var(--c-fg-16a34a)"
          drill={<DrillTileLinks drills={drill.dl('credits', 'movements behind this')} open={drill.open} onToggle={drill.toggle} />} />
        <Stat label="Net (credits − debits)" value={`${served('net', totals.net) >= 0 ? '+' : ''}${fmt(served('net', totals.net))}`}
          color={totals.net >= 0 ? 'var(--c-fg-059669)' : 'var(--c-fg-dc2626)'}
          drill={<DrillTileLinks drills={drill.dl('net', 'movements behind this')} open={drill.open} onToggle={drill.toggle} />} />
        <Stat label="Days · feed rows" value={`${dayCount} · ${totals.rows.toLocaleString()}`} />
      </div>

      {/* ONE panel open at a time, below the strip — the shared renderer (§CORE-17.3n). */}
      {drill.panel && <div style={{ marginBottom: 16 }}><DrillDownPanel panel={drill.panel} extraColumns={CELL_DRILL_COLUMNS} /></div>}

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <MarketStorePicker
          stores={storesForCascade}
          marketOptions={marketOptions}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        <CheckboxDropdown options={catOpts} value={fCats} onChange={setFCats}
          placeholder="Categories…" width={210} ariaLabel="Pay category filter" />
        <CheckboxDropdown options={typeOpts} value={fTypes} onChange={setFTypes}
          placeholder="Transaction types…" width={220} ariaLabel="Transaction type filter" />
        <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={showDaySubtotals} onChange={e => setShowDaySubtotals(e.target.checked)} /> Day subtotals
        </label>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.filter(r => !r.isDay).length} line(s)</span>
      </div>

      {unclassified.types.length > 0 && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 13, color: 'var(--c-fg-92400e)', background: 'var(--c-bg-fffbeb)', lineHeight: 1.55 }}>
          <strong>{fmt(unclassified.moved)}</strong> across <strong>{unclassified.types.length}</strong> payment
          type{unclassified.types.length === 1 ? '' : 's'} has no category, so it cannot be told apart as
          commission or rebate. {data?.categories?.note}
          <div style={{ marginTop: 6, fontSize: 12.5 }}>
            {unclassified.types.slice(0, 8).map(t => (
              <span key={t.tx_type} style={{ display: 'block' }}>· {t.tx_type} — {fmt(t.moved)} over {t.rows.toLocaleString()} row{t.rows === 1 ? '' : 's'}</span>
            ))}
            {unclassified.types.length > 8 && <span style={{ display: 'block' }}>· and {unclassified.types.length - 8} more — pick “{NO_CATEGORY_ID}” in Categories to see them all.</span>}
          </div>
        </div>
      )}

      {truncated && (
        <div className="card" style={{ padding: '8px 14px', marginBottom: 12, fontSize: 13, color: 'var(--c-fg-b45309)', background: 'var(--c-bg-fffbeb)' }}>
          The date range holds more feed rows than one report can load — totals below are incomplete. Narrow the dates.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: 'var(--c-fg-b91c1c)' }}>Error: {err}</div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          No processor money movements for {rangeLabel}{fStores.length || fMarkets.length || fTypes.length || fCats.length ? ' with these filters' : ''}.
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
            <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              {['Date', 'Transaction type', 'Category', 'Debits', 'Credits', 'Net', 'Rows'].map(h =>
                <th key={h} style={{ textAlign: h === 'Date' || h === 'Transaction type' || h === 'Category' ? 'left' : 'right', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => r.isDay ? (
                <tr key={`day_${r.date}`} style={{ borderTop: '1px solid var(--border)', background: 'var(--surface2)', fontWeight: 700 }}>
                  <td style={{ padding: '8px 12px', fontSize: 12.5 }}>{r.date}</td>
                  <td colSpan={2} style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)' }}>DAY TOTAL</td>
                  <td style={{ ...num, color: 'var(--c-fg-dc2626)' }}>{r.debits ? fmt(r.debits) : '—'}</td>
                  <td style={{ ...num, color: 'var(--c-fg-16a34a)' }}>{r.credits ? fmt(r.credits) : '—'}</td>
                  <td style={{ ...num, color: r.net >= 0 ? 'var(--c-fg-059669)' : 'var(--c-fg-dc2626)' }}>{r.net >= 0 ? '+' : ''}{fmt(r.net)}</td>
                  <td style={{ ...num, fontSize: 12, color: 'var(--text3)' }}>{r.rows.toLocaleString()}</td>
                </tr>
              ) : (
                <tr key={`${r.date}_${r.tx_type}_${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '9px 12px', fontSize: 13, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{r.date}</td>
                  <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>{r.tx_type}</td>
                  <td style={{ padding: '9px 12px', fontSize: 12.5, whiteSpace: 'nowrap' }}>
                    {r.category
                      ? <span>{r.category}</span>
                      : <span style={{ color: 'var(--c-fg-b45309)' }} title="This org has never mapped this payment type to a pay category, so it belongs to no bucket.">not classified</span>}
                  </td>
                  <td style={{ ...num, color: r.debits ? 'var(--c-fg-dc2626)' : 'var(--text3)' }}>{r.debits ? fmt(r.debits) : '—'}</td>
                  <td style={{ ...num, color: r.credits ? 'var(--c-fg-16a34a)' : 'var(--text3)' }}>{r.credits ? fmt(r.credits) : '—'}</td>
                  <td style={{ ...num, fontWeight: 700, color: r.net >= 0 ? 'var(--c-fg-059669)' : 'var(--c-fg-dc2626)' }}>{r.net >= 0 ? '+' : ''}{fmt(r.net)}</td>
                  <td style={{ ...num, fontSize: 12, color: 'var(--text3)' }}>{r.rows.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2)', fontWeight: 700 }}>
                <td colSpan={3} style={{ padding: '10px 12px', fontSize: 13 }}>TOTAL ({dayCount} day(s))</td>
                <td style={{ ...num, color: 'var(--c-fg-dc2626)' }}>{fmt(totals.debits)}</td>
                <td style={{ ...num, color: 'var(--c-fg-16a34a)' }}>{fmt(totals.credits)}</td>
                <td style={{ ...num, color: totals.net >= 0 ? 'var(--c-fg-059669)' : 'var(--c-fg-dc2626)' }}>{totals.net >= 0 ? '+' : ''}{fmt(totals.net)}</td>
                <td style={{ ...num, fontSize: 12, color: 'var(--text3)' }}>{totals.rows.toLocaleString()}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {feedsWithRows.length > 0 && (
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12, maxWidth: 860 }}>
          {feedsWithRows.map(f => (
            <span key={f.processor} style={{ display: 'block' }}>
              Source <b>{sourceLabel(f.source.split('.').pop())}</b> ({f.rows.toLocaleString()} rows): {f.classification}.
            </span>
          ))}
          Net = credits − debits at every grain; a positive net means the processor paid you more than it took that
          day. Stores with an unmapped feed key stay visible until a specific market/store is picked.
        </p>
      )}
    </div>
  )
}

function Stat({ label, value, color, drill }: { label: string; value: string; color?: string; drill?: React.ReactNode }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
    {drill}
  </div>
}
