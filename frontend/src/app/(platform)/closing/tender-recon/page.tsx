'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'
import { SetupNotice } from '@/lib/setupNotice'
import DrillDownPanel, { DrillTileLinks, useDrillTile, type DrillPanel, type DrillRow } from '@/components/DrillDownPanel'
import { narrowPanels, screenKey } from '@/lib/drill-narrow'

// X-Tender Recon — the POS "X report" tenders (commcalc.pos_tender_summary) vs the daily closing sheet
// employees submit (commcalc.daily_closing), per store, cash vs card. Reads GET /commcalc/x-tender-recon.
//
// RULE FIVE (§3d) retrofit (2026-08-03): standard core filter bar — store(s)/market(s) narrow the loaded
// rows client-side (pick-don't-type over the data actually present, per optionsFromRows); the existing
// Day/Month toggle + date/period input IS this page's period control (appended via `right`, periodMode
// 'none' so the bar doesn't render a second, competing period picker) and keeps driving the server query
// exactly as before. Tolerance stays module-specific, also appended. There is NO rep dimension in this
// per-store recon dataset — the picker renders nothing (StandardFilterBar hides an empty option list) per
// the dispatch note; it is not invented from another table. Market has no column on these rows either, so
// it's joined client-side from the same org-scoped `/closing/stores` roster this module already calls
// elsewhere (closing/page.tsx), matched against the recon row's `store` string.

export default function XTenderReconPage() {
  const [mode, setMode] = useState<'date' | 'period'>('date')
  const [date, setDate] = useState(() => localToday())
  const [period, setPeriod] = useState('')
  const [tolerance, setTolerance] = useState(1)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [onlyMismatch, setOnlyMismatch] = useState(false)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  function load() {
    setLoading(true)
    const q = mode === 'date' ? `date=${date}` : `period=${encodeURIComponent(period)}`
    api(`/api/v1/commcalc/x-tender-recon?${q}&tolerance=${tolerance}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [mode, date, period, tolerance])

  // Canonical, org-scoped store→market lookup (pick-don't-type §3b) — the same endpoint this module
  // already calls elsewhere (closing/page.tsx), not a new backend read.
  const [pStores, setPStores] = useState<any[]>([])
  useEffect(() => { apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {}) }, [])
  const marketByKey = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of pStores) {
      const mkt = s.market || ''
      if (!mkt) continue
      for (const k of [s.store_address, s.store_code, s.sfid]) {
        const key = (k || '').trim().toLowerCase()
        if (key && !m[key]) m[key] = mkt
      }
    }
    return m
  }, [pStores])
  const marketFor = (store: string) => marketByKey[(store || '').trim().toLowerCase()] || ''

  const allRows: any[] = data?.rows || []
  // Join market onto each row for filtering/options — the row's own `store` string is the only key this
  // recon has (POS X-report name or closing address/name/code, whichever matched — see the backend note),
  // so options for the store filter itself come straight from that field (data actually present).
  const joinedRows = useMemo(() => allRows.map(r => ({ ...r, market: marketFor(r.store) })), [allRows, marketByKey])
  const acc = useMemo(() => ({ store: (r: any) => r.store, market: (r: any) => r.market }), [])
  const opts = useMemo(() => optionsFromRows(joinedRows, acc), [joinedRows, acc])
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): distinct store/market pairs
  // from the SAME joined rows the rest of this page's filtering already uses — no new backend read.
  const storesForCascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    for (const r of joinedRows) {
      const id = (r.store || '').trim()
      if (id && !seen.has(id)) seen.set(id, { id, label: id, market: r.market || null })
    }
    return [...seen.values()]
  }, [joinedRows])
  const filtered = useMemo(() => filterRows(joinedRows, filt, acc), [joinedRows, filt, acc])
  const rows = filtered.filter(r => !onlyMismatch || !r.match)

  // THE TILES AND THEIR EVIDENCE ARE ONE FOLD (§CORE-17.3d) — this replaces the reported gap of
  // §CORE-17.3b rather than restating it.
  //
  // RULE FIVE says the tiles follow the filter selection, and this page's filters are client-side, so
  // the tiles used to be their own TypeScript reduce over `filtered` while the server folded the drill
  // panels over the whole range. Under any filter those are different populations, and a panel
  // explaining a figure the screen is not showing looks like proof of the wrong number — so the drill
  // was offered only where the two happened to agree.
  //
  // Now the server serves each drill row with the `row_key` of the report row it explains, and
  // `narrowPanels` restricts every panel to THIS SELECTION using the same store key the table filters
  // by. The tile then reads its number off the narrowed panel instead of reducing the rows again, so
  // there is one fold and the tile cannot drift from its evidence. The client reduce survives only as
  // the fallback for a figure no panel was served for (an older payload, or a panel the server capped
  // — see `narrowPanel`), and such a tile offers no drill.
  // `drills` is memoised rather than written inline: a fresh `{}` on every render would re-narrow
  // every panel on every render, and is the dependency eslint's exhaustive-deps rule objects to.
  const drills = useMemo<Record<string, DrillPanel>>(() => data?.drilldown || {}, [data])
  const selected = useMemo(
    () => new Set(filtered.map((r: { store?: string }) => screenKey({ store: r.store }))), [filtered])
  const panels = useMemo(
    () => narrowPanels(drills, (_r, k) => selected.has(k)), [drills, selected])
  const t = useMemo(() => {
    const sum = (k: string) => filtered.reduce((a: number, r: any) => a + (Number(r[k]) || 0), 0)
    const of = (k: string, fallback: () => number) =>
      panels[k] ? (panels[k].figure?.amount ?? 0) : fallback()
    return {
      pos_cash: of('pos_cash', () => sum('pos_cash')),
      closing_cash: of('closing_cash', () => sum('closing_cash')),
      cash_variance: of('cash_variance', () => sum('cash_variance')),
      pos_card: of('pos_card', () => sum('pos_card')),
      closing_card: of('closing_card', () => sum('closing_card')),
      card_variance: of('card_variance', () => sum('card_variance')),
      stores: filtered.length,
      mismatches: of('mismatches', () => filtered.filter((r: any) => !r.match).length),
    }
  }, [filtered, panels])
  const scope = mode === 'date' ? date : (period || 'period')

  // A figure with a narrowed panel agrees with it BY CONSTRUCTION — the tile is read off it. The
  // shared offer rule still gates on the panel existing, which is what a capped or unserved figure
  // falls through.
  const drill = useDrillTile(panels)
  const dl = drill.dl
  // A figure the fallback computed: the reader is told plainly why it has no rows, rather than left
  // to wonder where the link went.
  const unpanelled = useMemo(
    () => Object.keys(drills).filter(k => !panels[k]), [drills, panels])
  const DRILL_COLUMNS = [
    { header: 'Status', get: (r: DrillRow) => (r.fields?.match ? 'OK' : !r.fields?.in_closing ? 'no closing' : !r.fields?.in_pos ? 'no X report' : 'MISMATCH'),
      sortValue: (r: DrillRow) => (r.fields?.match ? 'OK' : !r.fields?.in_closing ? 'no closing' : !r.fields?.in_pos ? 'no X report' : 'MISMATCH') },
  ]

  function buildPayload(): ExportPayload {
    return {
      title: 'X-Tender Recon (POS vs Daily Closing)', subtitle: scope,
      filename: `x-tender-recon_${scope}`.replace(/\s+/g, '-'),
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Market', get: (r: any) => r.market },
        { header: 'POS cash', get: (r: any) => r.pos_cash, money: true },
        { header: 'Closing cash', get: (r: any) => r.closing_cash, money: true },
        { header: 'Cash Δ', get: (r: any) => r.cash_variance, money: true },
        { header: 'POS card', get: (r: any) => r.pos_card, money: true },
        { header: 'Closing card', get: (r: any) => r.closing_card, money: true },
        { header: 'Card Δ', get: (r: any) => r.card_variance, money: true },
        { header: 'POS other', get: (r: any) => r.pos_other, money: true },
        { header: 'Status', get: (r: any) => r.match ? 'OK' : (!r.in_closing ? 'no closing' : !r.in_pos ? 'no X report' : 'MISMATCH') },
      ] }],
    }
  }

  const vColor = (v: number) => Math.abs(v) > tolerance ? '#b91c1c' : 'var(--text3)'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 X-Tender Recon</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            POS <strong>X report</strong> tenders vs the <strong>daily closing sheet</strong> employees submit, per store —
            cash (store + bill-pay cash) and card (store + bill-pay credit). A <strong>variance</strong> beyond the tolerance flags a
            cash/card discrepancy to chase.
          </p>
        </div>
        {allRows.length > 0 && <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></div>}
      </div>

      {/* RULE FIVE core set (store(s)/market(s) — no rep dimension in this dataset, see header note) +
          the module's own period control (Day/Month + date/period input) and tolerance, appended.
          Market/store render via the shared cascade-checkbox <MarketStorePicker> (OWNER DIRECTIVE
          2026-08-04) — the owner named THIS page ("X-Tender") explicitly. */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="none"
        show={{ period: false, stores: false, markets: false, reps: true }}
        storeOptions={opts.stores} marketOptions={opts.markets} repOptions={opts.reps}
        storeLabel="Stores…" marketLabel="Markets…"
        right={
          <>
            <MarketStorePicker
              stores={storesForCascade}
              selectedMarkets={filt.markets} onMarketsChange={ids => setFilt(f => ({ ...f, markets: ids }))}
              selectedStores={filt.stores} onStoresChange={ids => setFilt(f => ({ ...f, stores: ids }))}
            />
            <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
              <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'date' ? 'var(--accent)' : 'transparent', color: mode === 'date' ? 'white' : 'var(--text2)' }} onClick={() => setMode('date')}>Day</button>
              <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'period' ? 'var(--accent)' : 'transparent', color: mode === 'period' ? 'white' : 'var(--text2)' }} onClick={() => { setMode('period'); if (!period) setPeriod(localToday().slice(0, 7)) }}>Month</button>
            </div>
            {mode === 'date'
              ? <input className="select" type="date" value={date} onChange={e => setDate(e.target.value)} />
              : <input className="select" type="month" value={period} onChange={e => setPeriod(e.target.value)} style={{ width: 150 }} />}
            <label style={{ fontSize: 12, color: 'var(--text2)' }}>± $
              <input className="select" type="number" value={tolerance} onChange={e => setTolerance(Number(e.target.value) || 0)} style={{ width: 64, marginLeft: 4 }} />
            </label>
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={onlyMismatch} onChange={e => setOnlyMismatch(e.target.checked)} /> Mismatches only
            </label>
          </>
        }
      />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: 'var(--c-fg-b91c1c)' }}>Error: {data.error}</div>
      ) : data?.ready === false ? (
        <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{data.note || <SetupNotice lead="Import an X report once this is switched on." detail="062_pos_tender_summary.sql" />}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label="POS cash" value={fmt(t.pos_cash || 0)} sub={`closing ${fmt(t.closing_cash || 0)}`}
              drill={drill} drills={dl('pos_cash', 'POS rows', 'closing_cash', 'closing rows')} />
            <Tile label="Cash variance" value={fmt(t.cash_variance || 0)} accent={Math.abs(t.cash_variance || 0) > tolerance ? '#b91c1c' : '#15803d'}
              drill={drill} drills={dl('cash_variance', 'these stores')} />
            <Tile label="POS card" value={fmt(t.pos_card || 0)} sub={`closing ${fmt(t.closing_card || 0)}`}
              drill={drill} drills={dl('pos_card', 'POS rows', 'closing_card', 'closing rows')} />
            <Tile label="Card variance" value={fmt(t.card_variance || 0)} accent={Math.abs(t.card_variance || 0) > tolerance ? '#b91c1c' : '#15803d'}
              drill={drill} drills={dl('card_variance', 'these stores')} />
            <Tile label="Stores" value={t.stores ?? 0} />
            <Tile label="Mismatches" value={t.mismatches ?? 0} accent={(t.mismatches || 0) > 0 ? '#b91c1c' : '#15803d'}
              drill={drill} drills={dl('mismatches', 'the mismatched stores')} />
          </div>

          {/* ONE panel at a time, below the strip. */}
          {drill.open && (
            <div style={{ marginBottom: 14 }}>
              <DrillDownPanel panel={drill.panel} extraColumns={DRILL_COLUMNS}
                emptyNote="No store in this range contributed to this figure." />
            </div>
          )}
          {/* A FIGURE WITHOUT EVIDENCE SAYS SO. Every tile here normally reads its number off its
              own narrowed panel, so a missing panel means the server capped its rows (too many to
              narrow honestly) or did not serve that figure at all — and the tile fell back to a
              reduce with nothing behind it. Saying which figures those are beats a vanished link. */}
          {unpanelled.length > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 12 }}>
              {unpanelled.length} figure(s) above could not be opened for this range — there are more
              rows behind them than the report serves at once, so narrowing them to your filter would
              leave rows out. Pick a shorter range or fewer stores to open them.
            </div>
          )}

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: 13 }}>By store — {scope}</span>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} shown</span>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>POS cash</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Closing cash</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Cash Δ</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>POS card</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Closing card</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Card Δ</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>POS other</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Status</th>
              </tr></thead>
              <tbody>
                {rows.map((r: any, i: number) => (
                  <tr key={r.store || i} style={{ borderTop: '1px solid var(--border)', background: r.match ? undefined : '#fffafa' }}>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.store}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.pos_cash)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.closing_cash)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600, color: vColor(r.cash_variance) }}>{fmt(r.cash_variance)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.pos_card)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.closing_card)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600, color: vColor(r.card_variance) }}>{fmt(r.card_variance)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text3)' }}>{r.pos_other ? fmt(r.pos_other) : '—'}</td>
                    <td style={{ padding: '7px 12px', fontSize: 12 }}>
                      {r.match ? <span style={{ color: 'var(--c-fg-15803d)' }}>✓ OK</span>
                        : !r.in_closing ? <span style={{ color: 'var(--c-fg-b45309)' }}>no closing sheet</span>
                          : !r.in_pos ? <span style={{ color: 'var(--c-fg-b45309)' }}>no X report</span>
                            : <span style={{ color: 'var(--c-fg-b91c1c)', fontWeight: 600 }}>⚠ mismatch</span>}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tender data for {scope}{onlyMismatch ? ' (no mismatches)' : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, sub, accent, drills, drill }: {
  label: string; value: any; sub?: string; accent?: string
  drills?: { key: string; label: string }[]
  drill?: { open: string | null; toggle: (k: string) => void }
}) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: accent || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
      {drill && drills && drills.length ? <DrillTileLinks drills={drills} open={drill.open} onToggle={drill.toggle} /> : null}
    </div>
  )
}
