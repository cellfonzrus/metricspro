'use client'
import { useState, useEffect, useMemo, useCallback, Suspense, Fragment } from 'react'
import { useUrlTab } from '@/lib/useUrlTab'
import { api, fmt } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { buildForecastMatrix, matrixExportColumns, matrixExportRows, FORECAST_METRICS, type ForecastMetric } from '@/lib/forecast-matrix'
import { owedSourceLabel } from '@/lib/owed-source'
import type { StoreOpt } from '@/lib/market-store-cascade'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// Device Forecasting & Vendor Payables (module 095). Reads the config-driven ledger built by
// POST /api/v1/payables/rebuild. Forecasting is phones-only; payables + due are per-IMEI.

// The tabs this page declares; `?tab=` is read through the ONE URL-tab reader (lib/useUrlTab.ts, index §19.40).
// A CARRIER'S DECLARATION, only the part the Payment Terms tab shows. Deliberately narrow: the row
// carries two dozen columns and this screen must not be able to send back one it never displayed.
type TermsRow = {
  carrier_id: string
  label?: string | null
  net_terms_days?: number | null
  due_date_mode?: string | null
  due_date_field?: string | null
}

const PAYABLES_TABS = ['forecast', 'payables', 'owed', 'map', 'terms'] as const
type Tab = typeof PAYABLES_TABS[number]
const sel = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' } as const
const STATUS_COLORS: Record<string, string> = {
  discrepancy: '#dc2626', due: '#d97706', offset: '#16a34a', open: '#6b7280', unconfigured: '#6b7280',
}

// One unmapped raw-model candidate → map it to a canonical name + carrier. Own local state so typing
// isn't lost on parent re-renders (defined at module scope, not inside the page component).
function MapRow({ cand, carriers, onSave }: any) {
  const [canonical, setCanonical] = useState(cand.raw_model)
  const [cid, setCid] = useState(cand.carrier_id || '')
  const [saving, setSaving] = useState(false)
  const cell = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }
  return (
    <tr>
      <td style={cell}><div style={{ fontSize: 12 }}>{cand.raw_model}</div><div style={{ fontSize: 11, color: 'var(--muted)' }}>{cand.side} · {cand.count}×</div></td>
      <td style={cell}><input value={canonical} onChange={e => setCanonical(e.target.value)} style={{ ...sel, width: '100%' }} /></td>
      <td style={cell}>
        <select value={cid} onChange={e => setCid(e.target.value)} style={sel}>
          <option value="">(carrier)</option>
          {carriers.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </td>
      <td style={cell}>
        <button disabled={saving} onClick={async () => { setSaving(true); try { await onSave(cand.raw_model, canonical, cid, cand.side) } finally { setSaving(false) } }}
          style={{ padding: '5px 12px', borderRadius: 6, border: 'none', background: 'var(--accent, var(--c-bg-2563eb))', color: 'var(--c-fg-ffffff)', fontSize: 12, cursor: 'pointer' }}>
          {saving ? '…' : 'Save'}
        </button>
      </td>
    </tr>
  )
}

/** One store's share of a date's owed total (`/payables/owed-by-date` → `by_store`, §COMMCALC-32).
 *  `store` empty means the build could not resolve the store, so the money cannot be debited to an
 *  account — it is shown as its own line rather than folded into a resolved store's figure. */
type OwedByStore = { store?: string | null; store_key?: string | null; market?: string | null; count: number; owed: number }

export default function PayablesPage() {
  return <Suspense fallback={null}><PayablesPageBody /></Suspense>
}

function PayablesPageBody() {
  const [tab, setTab] = useUrlTab(PAYABLES_TABS, 'payables')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  // forecast
  const [lookback, setLookback] = useState(7)
  const [horizon, setHorizon] = useState(7)
  const [fRows, setFRows] = useState<any[]>([])
  const [fMeta, setFMeta] = useState<any>({})
  // Forecast view: the per-line LIST, or the GRID a purchase order reads off (owner 2026-10-22:
  // "the forecasting should summarize in grid format to be exported to understand how many phones
  // are needed of a particular model per store"). Both are the SAME filtered rows — the grid is a
  // pivot of them (lib/forecast-matrix), never a second fetch, so they can never disagree.
  const [fView, setFView] = useState<'list' | 'grid'>('grid')
  const [fMetric, setFMetric] = useState<ForecastMetric>('recommend_order')
  // phone mapping
  const [candidates, setCandidates] = useState<any[]>([])
  const [mappings, setMappings] = useState<any[]>([])
  const [carriersList, setCarriersList] = useState<any[]>([])
  // payables
  const [status, setStatus] = useState('')
  const [pRows, setPRows] = useState<any[]>([])
  // The payables response's own meta, not its rows: `undated_excluded` is how many ledger rows the
  // date window could not judge, which the screen SAYS rather than silently dropping (§COMMCALC-25).
  const [pMeta, setPMeta] = useState<{ undated_excluded?: number; no_device_excluded?: number | null;
    no_device_note?: string }>({})
  // The rebuild's own complaints: a table or column the carrier's SETUP declares that the build
  // could not read. Silence here is what let a mis-declared cost source write 2,923 devices with no
  // price that looked exactly like a carrier nobody had configured (§COMMCALC-29).
  const [warns, setWarns] = useState<string[]>([])
  // WHEN THIS LEDGER LAST REBUILT ITSELF (index §COMMCALC-41). The ledger is rebuilt by the post-landing
  // hook now, so the question a tenant used to answer by pressing the button — "is what I am looking at
  // current?" — is answered on the page instead. One sentence, from the server's own one home: this screen
  // composes no wording of its own and makes no judgement about freshness.
  const [auto, setAuto] = useState<{ state?: string; tone?: string; sentence?: string } | null>(null)
  // THE CARRIER'S OWN DECLARATION, editable here (owner 2026-10-29: "Put the Net term days as 14 from
  // the back , programmable not hard code"). These are `commcalc.payable_source_map` rows served by the
  // endpoint that already existed — no second door, and nothing in code holds a number of days.
  const [terms, setTerms] = useState<TermsRow[]>([])
  const [termsDraft, setTermsDraft] = useState<Record<string, string>>({})
  // WHICH DAYS THE SERVER ACTUALLY MEASURED. The default (today -> +3 days) is computed by the
  // endpoint and returned, so the pickers show what was applied instead of this page holding a
  // second copy of the default that could drift from it (§COMMCALC-32).
  const [appliedWin, setAppliedWin] = useState<{ start?: string; end?: string; basis?: string } | null>(null)
  const [allDates, setAllDates] = useState(false)
  const [openDate, setOpenDate] = useState('')
  const [drill, setDrill] = useState<any | null>(null)
  // owed-by-date. WHO OWES WHAT on a date (§COMMCALC-32): the per-store split rides WITH the date
  // row, so it is shaped here rather than left untyped — a blank `store` is money whose store the
  // build could not resolve, which must stay visible instead of being folded into another store.
  const [oRows, setORows] = useState<any[]>([])
  const [oMeta, setOMeta] = useState<any>({})
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState<any>({ priority_ack_enabled: false, priority_window_pct: 25 })
  const [showSettings, setShowSettings] = useState(false)
  // RULE FIVE — the shared filter bar, now carrying the FULL core set for this screen: date RANGE +
  // market + store (pick-don't-type, org-scoped options). Reps are omitted and that deviation is the
  // documented kind: a payable is a fact about a device and a vendor, not about who sold it.
  //
  // WHO APPLIES WHAT. The date range is applied SERVER-SIDE on all three data tabs (a forecast row is
  // an aggregate over the window and carries no date for a client filter to read). Store/market are
  // applied client-side over the loaded rows, matched on the row's canonical `store_key`. The
  // Daily-Owed tab is a date aggregate with no store dimension, so it honors a SINGLE selected store
  // server-side (0 or >1 selected → all stores). org_id is injected by the shared client from the
  // caller's JWT — never hardcoded.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const owedStore = filt.stores.length === 1 ? filt.stores[0] : ''
  // The market half of the bar is MULTI-select, and the Daily Owed tab applies it SERVER-side (a date
  // aggregate carries no store dimension for a client-side predicate to read). One joined key, so the
  // effect below depends on a plain value and the query sends exactly what the effect watched.
  const marketKey = filt.markets.join(',')

  // The ledger rows carry a store but NO market, so the cascade's market vocabulary comes from the
  // org roster (the same org-scoped pick-don't-type source /core/filter-options serves every other bar).
  const [roster, setRoster] = useState<StoreOpt[]>([])
  // The org's canonical market OPTION list, served by the same endpoint (core.scope.org_market_options
  // §13c — the canonical vocabulary ∪ this surface's own stamps). Used as the market options whenever
  // the bar renders its plain pickers (roster still loading / failed); in cascade mode the roster's
  // per-store stamps — resolved through the same canonical union resolver server-side — drive it.
  const [rosterMarkets, setRosterMarkets] = useState<string[]>([])
  useEffect(() => { api(`/api/v1/payables/settings`).then(setSettings).catch(() => {}) }, [])
  // ⚠️ NOT /core/filter-options. That one keys stores on `storeops.stores.address` ("4640 Diversey
  // Chicago"), while every row on this page carries the store_mapping spelling ("4640-A W Diversey
  // Ave") — MEASURED 2026-08-11: 0 of luxelink's 20 stores matched, so the roster's options could
  // never select a row and the market picker filtered the whole table away. /payables/filter-options
  // serves the vocabulary the DATA uses, with the market attached.
  useEffect(() => {
    apiCached(`/api/v1/payables/filter-options`, LOOKUP)
      .then((d: any) => {
        // THE OPTION'S id IS THE CANONICAL STORE KEY, its label the roster's spelling (owner report
        // 2026-10-22: "the filter for market brings up the forecasting but the filter for store does
        // not bring up anything"). The market half always worked because every row is STAMPED with a
        // resolved market; the store half compared the option's spelling against the row's own, and
        // the feeds spell a store differently from the roster — measured live that day, 13 of 33
        // asset_ledger spellings and 15 of 37 ledger spellings matched NO option, so picking those
        // stores returned nothing. Both sides now carry `store_key` (account.store_identity, §64)
        // and the comparison is made on that, so a spelling difference can no longer hide rows.
        setRoster((d?.stores || []).map((x: any) => ({ id: x.store_key || x.store, label: x.store, market: x.market || null })))
        setRosterMarkets((d?.markets || []).map((m: any) => String(m)).filter(Boolean))
      })
      .catch(() => { setRoster([]); setRosterMarkets([]) })
  }, [])
  // The DATE RANGE is applied SERVER-SIDE on all three data tabs, not client-side over loaded rows.
  // It has to be: a forecast row is an AGGREGATE over the sales window and carries no date of its
  // own, so there is nothing on it for a client-side date filter to read — the window must reach the
  // query that builds it. Payables/Daily Owed window on `due_date` for the same consistency, which
  // also means `limit` counts rows inside the range instead of rows before it.
  useEffect(() => { if (tab === 'forecast') loadForecast() }, [tab, lookback, horizon, filt.period, filt.periodTo])
  useEffect(() => { if (tab === 'payables') loadPayables() }, [tab, status, filt.period, filt.periodTo, allDates])
  useEffect(() => { if (tab === 'owed') loadOwed() }, [tab, owedStore, filt.period, filt.periodTo, marketKey, allDates])
  useEffect(() => { if (tab === 'map') loadMap() }, [tab])
  useEffect(() => { if (tab === 'terms') loadTerms() }, [tab])
  // WHEN THIS LEDGER LAST REBUILT ITSELF (§COMMCALC-41) — read on the same tab change the rest of the
  // page reads on, deliberately not in an effect of its own.
  useEffect(() => { loadAuto() }, [tab])

  async function loadAuto() {
    try {
      const r = await api(`/api/v1/payables/auto-build`)
      setAuto(r?.auto || null)
    } catch {
      setAuto(null)          // the page works without it; freshness is never guessed from the client
    }
  }

  async function rebuild() {
    setBusy(true); setMsg('Rebuilding ledger… (may take a minute for a full rebuild)')
    try {
      const r = await api(`/api/v1/payables/rebuild`, { method: 'POST' })
      const sc = r.status_counts || {}
      setWarns(Array.isArray(r.warnings) ? r.warnings : [])
      setMsg(`Rebuilt ${r.written} devices across ${r.carriers} carrier(s): ` +
        Object.entries(sc).map(([k, v]) => `${v} ${k}`).join(', ') +
        (r.flags_written != null ? ` · ${r.flags_written} discrepancy flags` : ''))
      if (tab === 'payables') loadPayables(); else if (tab === 'forecast') loadForecast(); else loadOwed()
      loadAuto()
    } catch (e: any) { setWarns([]); setMsg('Rebuild error (it may still be completing server-side): ' + e.message) }
    setBusy(false)
  }

  async function loadForecast() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ lookback: String(lookback), horizon: String(horizon) })
      if (filt.period) qs.set('start', filt.period)      // the shared bar's date RANGE — the sales window
      if (filt.periodTo) qs.set('end', filt.periodTo)
      const d = await api(`/api/v1/payables/forecast?${qs}`); setFRows(d.rows || []); setFMeta(d)
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadMap() {
    setLoading(true)
    try {
      const [c, m] = await Promise.all([
        api(`/api/v1/payables/phone-map/candidates`),
        api(`/api/v1/payables/phone-map`),
      ])
      setCandidates(c.rows || []); setCarriersList(c.carriers || m.carriers || []); setMappings(m.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadTerms() {
    setLoading(true)
    try {
      const d = await api(`/api/v1/payables/source-maps`)
      setTerms(d.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function saveTerms(carrierId: string, days: string) {
    // AN EMPTY BOX MEANS "NOT DECLARED", which is not the same as zero: Boost's map leaves it unset
    // because the carrier stamps a real due date on every row, and 0 would mean "due the day it ships".
    const n = days.trim() === '' ? null : Number(days)
    if (n !== null && (!Number.isInteger(n) || n < 0)) { setMsg('Net terms must be a whole number of days, or blank for "not declared".'); return }
    setBusy(true); setMsg('')
    try {
      // ONE field goes up. The endpoint patches an existing carrier rather than demanding the whole
      // declaration back, so a box this screen never showed cannot be overwritten by saving this one.
      const r = await api(`/api/v1/payables/source-maps`, { method: 'POST', body: JSON.stringify({ carrier_id: carrierId, net_terms_days: n }) })
      setMsg(r?.auto?.queued
        ? 'Saved. The due dates are being recalculated from the new terms — nothing else to press.'
        : 'Saved. The due dates restate on the next rebuild of this ledger.')
      loadTerms(); loadAuto()
    } catch (e) { setMsg('Could not save: ' + (e instanceof Error ? e.message : String(e))) }
    setBusy(false)
  }
  async function saveMapping(raw: string, canonical: string, cid: string, side: string) {
    await api(`/api/v1/payables/phone-map`, { method: 'POST', body: JSON.stringify({ raw_model: raw, canonical_model: canonical, carrier_id: cid || null, side }) })
    setCandidates(prev => prev.filter(c => c.raw_model !== raw))    // drop the mapped one locally (candidates rescan is heavy)
    api(`/api/v1/payables/phone-map`).then((m: any) => setMappings(m.rows || [])).catch(() => {})
  }
  async function deleteMapping(id: string) {
    try { await api(`/api/v1/payables/phone-map/${id}`, { method: 'DELETE' }); loadMap() }
    catch (e: any) { setMsg(e.message) }
  }
  async function loadPayables() {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      if (status) qs.set('status', status)
      if (filt.period) qs.set('start', filt.period)      // windows on due_date
      if (filt.periodTo) qs.set('end', filt.periodTo)
      if (allDates) qs.set('all_dates', 'true')
      const d = await api(`/api/v1/payables/payables${qs.toString() ? `?${qs}` : ''}`); setPRows(d.rows || []); setPMeta(d || {})
      setAppliedWin(d.window || null)
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadOwed() {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      if (owedStore) qs.set('store', owedStore)   // date-aggregate has no store dim → single-store server filter
      if (filt.period) qs.set('start', filt.period)
      if (filt.periodTo) qs.set('end', filt.periodTo)
      if (marketKey) qs.set('market', marketKey)   // the same bar's market half (RULE FIVE), multi-select
      if (allDates) qs.set('all_dates', 'true')
      const d = await api(`/api/v1/payables/owed-by-date${qs.toString() ? `?${qs}` : ''}`); setORows(d.rows || []); setOMeta(d || {})
      setAppliedWin(d.window || null)
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function openDrill(imei: string) {
    try { setDrill(await api(`/api/v1/payables/offsets/${encodeURIComponent(imei)}`)) }
    catch (e: any) { setMsg(e.message) }
  }
  async function saveSettings(patch: any) {
    const next = { ...settings, ...patch }; setSettings(next)
    try { await api(`/api/v1/payables/settings`, { method: 'PUT', body: JSON.stringify(patch) }) }
    catch (e: any) { setMsg(e.message) }
  }

  // Pick-don't-type filter options from the ALREADY-org-scoped rows the page loaded (forecast ∪ payables
  // carry `store`; `market` shows only if the ledger has it → the market picker self-hides when absent).
  // The store a selection BINDS to is the row's canonical key, never the feed's spelling (see the
  // roster comment above). `store` stays the display value everywhere it is printed.
  const facc = { store: (r: any) => r.store_key || r.store, market: (r: any) => r.market }
  const filterOpts = useMemo(() => optionsFromRows([...fRows, ...pRows], facc), [fRows, pRows])   // eslint-disable-line react-hooks/exhaustive-deps
  // key -> the spelling to SHOW for it, first seen in the loaded rows. A store present in the data
  // but missing from the roster must stay selectable, and it must read as its own name.
  const rowStoreLabels = useMemo(() => {
    const m = new Map<string, string>()
    for (const r of [...fRows, ...pRows]) {
      const k = String(r.store_key || r.store || '').trim()
      const lbl = String(r.store || '').trim()
      if (k && lbl && !m.has(k)) m.set(k, lbl)
    }
    return m
  }, [fRows, pRows])
  // Cascade options = the org roster (which carries the markets) UNION any store present in the loaded
  // rows but missing from the roster — a store that genuinely has payables must never become unselectable
  // just because it isn't on the roster yet.
  const cascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    roster.forEach(r => seen.set(r.id.trim().toLowerCase(), r))
    filterOpts.stores.forEach(st => {
      const k = st.trim().toLowerCase()
      if (!seen.has(k)) seen.set(k, { id: st, label: rowStoreLabels.get(st) || st, market: null })
    })
    return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label))
  }, [roster, filterOpts.stores, rowStoreLabels])

  const fRowsF = useMemo(() => filterRows(fRows, filt, facc), [fRows, filt])   // eslint-disable-line react-hooks/exhaustive-deps
  const pRowsF = useMemo(() => filterRows(pRows, filt, facc), [pRows, filt])   // eslint-disable-line react-hooks/exhaustive-deps

  // Click-a-header sorting (owner 2026-08-10, "for all reports"). One hook per table so a column picked
  // on Payables doesn't follow you to Forecast, where it has no meaning.
  const getCell = useCallback((r: any, field: string) => r?.[field], [])
  const fSort = useTableSort(fRowsF, getCell)
  // THE GRID a purchase order reads off: stores down the side, models across the top, built from the
  // SAME filtered rows the list shows (lib/forecast-matrix - a pivot, never a second derivation).
  const fMatrix = useMemo(() => buildForecastMatrix(fRowsF, fMetric), [fRowsF, fMetric])
  const fGridRows = useMemo(() => matrixExportRows(fMatrix), [fMatrix])
  const fGridCols = useMemo(() => matrixExportColumns(fMatrix) as ExportColumn[], [fMatrix])
  const fMetricLabel = FORECAST_METRICS.find(x => x.id === fMetric)?.label || 'Units'
  const pSort = useTableSort(pRowsF, getCell)
  const oSort = useTableSort(oRows, getCell)

  const th = { textAlign: 'left' as const, padding: '8px 10px', borderBottom: '2px solid var(--border)', fontSize: 12, color: 'var(--muted)' }
  const td = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }

  // RULE FOUR export — reflects the ACTIVE tab (what-you-see-is-what-exports). The Phone-Mapping tab is a
  // config surface (mapping input), so it is exempt (no export bar there).
  const exportSpec = (): { title: string; filename: string; columns: ExportColumn[]; rows: any[] } | null => {
    // THE GRID IS WHAT EXPORTS WHEN THE GRID IS WHAT YOU SEE (RULE FOUR). Same pivot, same cells -
    // the spreadsheet a buyer opens is the screen they were reading, one column per model.
    if (tab === 'forecast' && fView === 'grid') return {
      title: `Device Forecast — ${fMetricLabel} by store × model`,
      filename: `device_forecast_grid_${fMetric}`, rows: fGridRows, columns: fGridCols,
    }
    if (tab === 'forecast') return {
      title: 'Device Forecast', filename: 'device_forecast', rows: fRowsF,
      columns: [
        { header: 'Carrier', field: 'carrier', get: (r: any) => r.carrier },
        { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store || '' },
        { header: 'Model', field: 'device_model', get: (r: any) => r.device_model },
        { header: 'Sold', get: (r: any) => r.units },
        { header: 'Velocity/day', get: (r: any) => r.avg_daily_velocity },
        { header: 'Projected', get: (r: any) => r.projected_demand },
        { header: 'On hand', get: (r: any) => r.on_hand },
        { header: 'Order', get: (r: any) => r.recommend_order },
      ],
    }
    if (tab === 'payables') return {
      title: 'Vendor Payables (per IMEI)', filename: 'vendor_payables', rows: pRowsF,
      columns: [
        { header: 'IMEI', field: 'imei', get: (r: any) => r.imei },
        { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store },
        { header: 'Model', field: 'device_model', get: (r: any) => r.device_model },
        { header: 'Owed', money: true, get: (r: any) => r.owed ?? 0 },
        { header: 'Rebate', money: true, get: (r: any) => r.rebate_amount ?? 0 },
        { header: 'Net owed', money: true, get: (r: any) => r.net_owed ?? 0 },
        { header: 'Due', field: 'due_date', type: 'date', get: (r: any) => r.due_date || '' },
        { header: 'Status', field: 'status', get: (r: any) => r.status },
      ],
    }
    if (tab === 'owed') return {
      title: 'Daily Owed', filename: 'daily_owed', rows: oRows,
      columns: [
        { header: 'Due date', field: 'due_date', type: 'date', get: (r: any) => r.due_date },
        { header: 'Devices', get: (r: any) => r.count },
        { header: 'Owed', money: true, get: (r: any) => r.owed },
      ],
    }
    return null
  }
  const exp = exportSpec()

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Device Forecasting &amp; Vendor Payables</h1>
          <p style={{ color: 'var(--muted)', fontSize: 13, margin: '4px 0 0' }}>
            Per-IMEI owed vs due, reconciled against sold + rebate received — and phones-only stock forecasting.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowSettings(s => !s)}
            style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }}>⚙ Settings</button>
          <button onClick={rebuild} disabled={busy}
            style={{ padding: '8px 16px', borderRadius: 8, border: 'none', background: 'var(--accent, var(--c-bg-2563eb))', color: 'var(--c-fg-ffffff)', fontWeight: 600, cursor: busy ? 'wait' : 'pointer' }}>
            {busy ? 'Rebuilding…' : '↻ Rebuild ledger'}
          </button>
        </div>
      </div>
      {showSettings && (
        <div style={{ marginTop: 12, padding: 14, borderRadius: 10, background: 'var(--surface)', border: '1px solid var(--border)', display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={!!settings.priority_ack_enabled} onChange={e => saveSettings({ priority_ack_enabled: e.target.checked })} />
            Require clock-in acknowledgment of priority phones
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            Priority window (final %):
            <input type="number" min={1} max={100} value={settings.priority_window_pct}
              onChange={e => setSettings((s: any) => ({ ...s, priority_window_pct: +e.target.value }))}
              onBlur={e => saveSettings({ priority_window_pct: +e.target.value })} style={{ ...sel, width: 70 }} />
          </label>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>The % applies on the next Rebuild.</span>
        </div>
      )}
      {/* WHICH DAYS YOU ARE LOOKING AT, and that nobody typed them (owner 2026-10-26: "should
          auaomatically pick teh dates from today and 3 days ahead ... the month and date shoudl not be
          entered manually"). The dates come from the RESPONSE, so this line can never claim a window
          the server did not apply. */}
      {(tab === 'payables' || tab === 'owed') && (
        <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 12, color: 'var(--text2)' }}>
          {appliedWin?.start ? (
            <span>Showing payments due <b>{appliedWin.start}</b> to <b>{appliedWin.end}</b>
              {appliedWin.basis === 'default_ahead' && <> — picked automatically (today plus the next 3 days); use the date range above for any other period</>}
            </span>
          ) : <span>Showing <b>all dates</b></span>}
          <button onClick={() => setAllDates(v => !v)}
            style={{ padding: '4px 10px', borderRadius: 7, border: '1px solid var(--border)', background: allDates ? 'var(--accent, var(--c-bg-2563eb))' : 'var(--surface)', color: allDates ? 'var(--c-fg-ffffff)' : 'var(--text)', cursor: 'pointer', fontSize: 12 }}>
            {allDates ? '✓ All dates' : 'All dates'}
          </button>
        </div>
      )}
      {auto?.sentence && (
        <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 8, fontSize: 13,
          background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <strong style={{ fontWeight: 600 }}>
            {auto.tone === 'error' ? '\u26a0 This ledger updates itself, and the last attempt did not finish.'
              : 'This ledger updates itself.'}
          </strong>{' '}{auto.sentence}{' '}
          <span style={{ opacity: 0.7 }}>The Rebuild button is only there if you want it rebuilt right now.</span>
        </div>
      )}
      {msg && <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 13 }}>{msg}</div>}
      {warns.length > 0 && (
        <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--c-fg-d97706)', fontSize: 13 }}>
          <b style={{ color: 'var(--c-fg-d97706)' }}>The rebuild finished, but part of the setup could not be read.</b>
          <br />Devices covered by this read no amount, which is NOT the same as owing nothing.
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {warns.map((w, i) => <li key={i} style={{ marginTop: 2 }}>{w}</li>)}
          </ul>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, margin: '18px 0 14px', flexWrap: 'wrap' }}>
        {(['payables', 'forecast', 'owed', 'map', 'terms'] as Tab[]).map(t => (
          <button key={t} onClick={() => setTab(t)}
            style={{ padding: '7px 14px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, cursor: 'pointer',
              background: tab === t ? 'var(--accent, #2563eb)' : 'var(--surface)', color: tab === t ? '#fff' : 'var(--text)', fontWeight: tab === t ? 600 : 400 }}>
            {t === 'payables' ? 'Payables (per IMEI)' : t === 'forecast' ? 'Forecast (phones)' : t === 'owed' ? 'Daily Owed' : t === 'map' ? 'Phone Mapping' : 'Payment Terms'}
          </button>
        ))}
        {tab === 'forecast' && (
          <>
            {/* LIST or GRID (owner 2026-10-22). The grid is the orderable shape: one row per store,
                one column per model, every cell a unit count you can put on a purchase order. */}
            <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)' }}>
              {(['grid', 'list'] as const).map(v => (
                <button key={v} onClick={() => setFView(v)}
                  style={{ padding: '7px 12px', border: 'none', fontSize: 13, cursor: 'pointer',
                    background: fView === v ? 'var(--accent, var(--c-bg-2563eb))' : 'var(--surface)',
                    color: fView === v ? 'var(--c-fg-ffffff)' : 'var(--text)', fontWeight: fView === v ? 600 : 400 }}>
                  {v === 'grid' ? 'Grid' : 'List'}
                </button>
              ))}
            </div>
            {fView === 'grid' && (
              <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                Show: <select value={fMetric} onChange={e => setFMetric(e.target.value as ForecastMetric)} style={sel}>
                  {FORECAST_METRICS.map(x => <option key={x.id} value={x.id} title={x.tip}>{x.label}</option>)}
                </select>
              </label>
            )}
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              Look-back (days): <input type="number" min={1} max={365} value={lookback} onChange={e => setLookback(+e.target.value || 7)} style={{ ...sel, width: 64 }}
                disabled={!!(filt.period || filt.periodTo)} title={(filt.period || filt.periodTo) ? 'A date range is picked, so the window is that range' : ''} />
            </label>
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              Order for next (days): <input type="number" min={1} max={365} value={horizon} onChange={e => setHorizon(+e.target.value || 7)} style={{ ...sel, width: 64 }} />
            </label>
          </>
        )}
        {tab === 'payables' && (
          <select value={status} onChange={e => setStatus(e.target.value)} style={sel}>
            <option value="">All statuses</option>
            <option value="discrepancy">Discrepancy (sold, no rebate)</option>
            <option value="due">Due (unsold, past due)</option>
            <option value="offset">Offset (rebate received)</option>
            <option value="open">Open</option>
          </select>
        )}
        {exp && exp.rows.length > 0 && (
          <>
            <div style={{ flex: 1 }} />
            <ReportExportBar title={exp.title} filename={exp.filename} columns={exp.columns} rows={exp.rows} />
          </>
        )}
      </div>

      {/* PLATFORM-WIDE (owner directive 2026-09-04: "the cellfonz r us has the market and store filter
          but luxelink does not, need to be platform wide"): the bar is gated on the CANONICAL org
          roster ∪ the loaded rows — never on the rows alone. The old gate keyed on optionsFromRows,
          so a tenant whose rows carried no store (every Total/MA device before the store-attribution
          fix) lost the whole filter bar; the roster (/payables/filter-options → core.scope §13c
          canonical enumeration) exists for every tenant, so the bar now renders for every tenant.
          marketOptions rides the same endpoint's canonical org_market_options list (∪ row stamps)
          for the non-cascade fallback; in cascade mode the roster's per-store market stamps —
          resolved server-side through the same canonical union resolver — drive the picker. */}
      {tab !== 'map' && tab !== 'terms' && (
        <StandardFilterBar value={filt} onChange={setFilt} show={{ reps: false }} periodMode="range"
          cascadeStores={cascade}
          storeOptions={cascade.map(c => ({ id: c.id, label: c.label }))}
          marketOptions={[...new Set([...rosterMarkets, ...filterOpts.markets])].sort()} />
      )}

      {loading && <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>}

      {tab === 'forecast' && !loading && (
        <>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
            {/* THE WINDOW IS NAMED, never implied. `window` comes back from the endpoint (the one
                home resolved it), so the screen states the days it actually measured rather than
                echoing whichever control you last touched. */}
            {fMeta.window?.basis === 'explicit_range'
              ? <>Velocity over <b>{fMeta.window.start || '…'} to {fMeta.window.end || '…'}</b> ({fMeta.window.days} day{fMeta.window.days === 1 ? '' : 's'})</>
              : <>Velocity from the last {fMeta.window?.days ?? fMeta.lookback ?? lookback} days</>}
            {' → '}order for the next {fMeta.horizon ?? horizon} days, per carrier.
            {(fMeta.unmapped ?? 0) > 0 && <> · <button onClick={() => setTab('map')} style={{ background: 'none', border: 'none', color: 'var(--c-fg-d97706)', cursor: 'pointer', fontSize: 12, padding: 0, textDecoration: 'underline' }}>{fMeta.unmapped} unmapped model(s)</button> — map them so sales &amp; stock line up.</>}
            {(fMeta.unassigned_store ?? 0) > 0 && <> · <b>{fMeta.unassigned_store}</b> row(s) with no store — a Total/marketplace order is placed against the dealer account, so its store is resolved from the POS line that sold the device; these could not be resolved.</>}
          </div>
          {/* ── THE GRID (owner 2026-10-22) — stores down the side, models across the top ────────
              Every cell is the chosen figure for that store and that model; a store with none of a
              model shows a dash rather than a 0, so what you must actually buy stands out. Carrier
              is folded away here on purpose: a model belongs to one carrier, and the buyer's
              question is "how many of this phone, for this store". The per-carrier line is one
              click away on List, and the grid respects every filter above it. */}
          {fView === 'grid' && (
            fMatrix.stores.length === 0 ? (
              <div className="card" style={{ padding: 20, fontSize: 13, color: 'var(--text2)', maxWidth: 820 }}>
                No device sales landed in this window, so there is nothing to project. Widen the date
                range (or the look-back), or check that the sales feed has reached these days.
              </div>
            ) : (
              <>
                {fMatrix.empty && (
                  <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
                    Every cell is zero — on current velocity, stock on hand covers the next
                    {' '}{fMeta.horizon ?? horizon} days. That is an answer, not an empty grid.
                  </div>
                )}
                <div style={{ overflowX: 'auto', marginBottom: 8 }}>
                  <table style={{ borderCollapse: 'collapse', minWidth: '100%' }}>
                    <thead>
                      <tr>
                        <th style={{ ...th, position: 'sticky', left: 0, background: 'var(--surface)', minWidth: 190 }}>Store</th>
                        {fMatrix.models.map(mo => (
                          <th key={mo} style={{ ...th, textAlign: 'right', whiteSpace: 'nowrap' }} title={mo}>{mo}</th>
                        ))}
                        <th style={{ ...th, textAlign: 'right', fontWeight: 700 }}>Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {fMatrix.stores.map(st => (
                        <tr key={st.key}>
                          <td style={{ ...td, position: 'sticky', left: 0, background: 'var(--surface)', fontWeight: 500 }}>
                            {st.label}
                          </td>
                          {fMatrix.models.map(mo => {
                            const v = fMatrix.cell(st.key, mo)
                            return (
                              <td key={mo} style={{ ...td, textAlign: 'right',
                                fontWeight: v > 0 && fMetric === 'recommend_order' ? 700 : 400,
                                color: v > 0 && fMetric === 'recommend_order' ? 'var(--c-fg-d97706)' : v === 0 ? 'var(--muted)' : 'inherit' }}>
                                {v === 0 ? '—' : v}
                              </td>
                            )
                          })}
                          <td style={{ ...td, textAlign: 'right', fontWeight: 700 }}>{fMatrix.storeTotal(st.key)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td style={{ ...td, position: 'sticky', left: 0, background: 'var(--surface)', fontWeight: 800 }}>TOTAL</td>
                        {fMatrix.models.map(mo => (
                          <td key={mo} style={{ ...td, textAlign: 'right', fontWeight: 800 }}>{fMatrix.modelTotal(mo)}</td>
                        ))}
                        <td style={{ ...td, textAlign: 'right', fontWeight: 800 }}>{fMatrix.grandTotal}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
                <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                  {fMetricLabel} · {fMatrix.stores.length} store{fMatrix.stores.length === 1 ? '' : 's'} ×
                  {' '}{fMatrix.models.length} model{fMatrix.models.length === 1 ? '' : 's'}. Export carries
                  every column, including the ones scrolled off to the right.
                </div>
              </>
            )
          )}
          {fView === 'list' && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <SortableTh field="carrier" sort={fSort.sort} onSort={fSort.toggle} style={th}>Carrier</SortableTh>
              <SortableTh field="store" sort={fSort.sort} onSort={fSort.toggle} style={th}>Store</SortableTh>
              <SortableTh field="device_model" sort={fSort.sort} onSort={fSort.toggle} style={th}>Model</SortableTh>
              <SortableTh field="units" sort={fSort.sort} onSort={fSort.toggle} style={th}>Sold ({fMeta.lookback ?? lookback}d)</SortableTh>
              <SortableTh field="avg_daily_velocity" sort={fSort.sort} onSort={fSort.toggle} style={th}>Velocity/day</SortableTh>
              <SortableTh field="projected_demand" sort={fSort.sort} onSort={fSort.toggle} style={th}>Projected ({fMeta.horizon ?? horizon}d)</SortableTh>
              <SortableTh field="on_hand" sort={fSort.sort} onSort={fSort.toggle} style={th}>On hand</SortableTh>
              <SortableTh field="recommend_order" sort={fSort.sort} onSort={fSort.toggle} style={th}>Order</SortableTh>
            </tr></thead>
            <tbody>{fSort.sorted.map((r: any, i: number) => (
              <tr key={i}><td style={td}>{r.carrier}{!r.mapped && <span title="unmapped model — map it on the Phone Mapping tab" style={{ color: 'var(--c-fg-d97706)' }}> •</span>}</td><td style={td}>{r.store || <span style={{ color: 'var(--muted)' }}>(unassigned)</span>}</td><td style={td}>{r.device_model}</td><td style={td}>{r.units}</td><td style={td}>{r.avg_daily_velocity}</td><td style={td}>{r.projected_demand}</td><td style={td}>{r.on_hand}</td>
                <td style={{ ...td, fontWeight: r.recommend_order > 0 ? 700 : 400, color: r.recommend_order > 0 ? 'var(--c-fg-d97706)' : 'inherit' }}>{r.recommend_order}</td></tr>
            ))}</tbody>
          </table>
          )}
        </>
      )}

      {/* STATUS LEGEND (owner 2026-08-10: "Payable per imei shows discrepancy — what does that mean and
          how to address that"). Four one-line definitions + the action, so the word on the row is
          self-explanatory instead of needing someone to remember the engine's routing rules. */}
      {tab === 'payables' && !loading && (
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10, lineHeight: 1.65, maxWidth: 960 }}>
          <b style={{ color: STATUS_COLORS.discrepancy }}>Discrepancy</b> = the device was <b>sold</b> but
          <b> no equipment rebate / reimbursement came back</b> — the dealer is still carrying its cost. Fix
          it by loading the rebate source (or the vendor invoice, if the cost itself is missing); it clears
          to <b style={{ color: STATUS_COLORS.offset }}>Offset</b> once a rebate ≥ the amount owed lands.
          {' · '}<b style={{ color: STATUS_COLORS.due }}>Due</b> = unsold and past its due date.
          {' · '}<b>Open</b> = inside the window, nothing to do yet.
          {' · '}An <b>Owed</b> of “—” means no invoice has priced this device — a discrepancy at $0 is a
          <i> missing rebate on an unpriced device</i>, not a $0 loss.
          {/* A DATE RANGE WINDOWS `due_date`, AND AN UNDATED ROW FALLS OUT OF EVERY RANGE. That is
              the right way to fail (guessing a date would invent one) but it is invisible, so it is
              SAID: these rows are the unpriced devices above, which have no invoice and therefore no
              due date. Without this line their absence reads as "nothing is owed in this range". */}
          {(pMeta.undated_excluded ?? 0) > 0 && (
            <><br /><b style={{ color: 'var(--c-fg-d97706)' }}>{pMeta.undated_excluded}</b> row(s) carry
            {' '}<b>no due date</b> and are therefore outside the date range you picked — clear the range to see them.</>
          )}
          {/* THE LINES THAT ARE NOT PAYABLES AT ALL (§COMMCALC-32). "Product Not Available" was never
              missing data: the customer brought their own phone, so no handset was bought and nothing
              can be owed. They are counted here rather than dropped, because a list that silently
              sheds rows is how a real one goes missing unnoticed. */}
          {(pMeta.no_device_excluded ?? 0) > 0 && (
            <><br /><b>{pMeta.no_device_excluded}</b> line(s) are <b>not device payables</b> and are not
            listed: {pMeta.no_device_note}</>
          )}
        </div>
      )}
      {tab === 'payables' && !loading && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <SortableTh field="imei" sort={pSort.sort} onSort={pSort.toggle} style={th}>IMEI</SortableTh>
            <SortableTh field="store" sort={pSort.sort} onSort={pSort.toggle} style={th}>Store</SortableTh>
            <SortableTh field="device_model" sort={pSort.sort} onSort={pSort.toggle} style={th}>Model</SortableTh>
            <SortableTh field="owed" sort={pSort.sort} onSort={pSort.toggle} style={th}>Owed</SortableTh>
            <SortableTh field="rebate_amount" sort={pSort.sort} onSort={pSort.toggle} style={th}>Rebate</SortableTh>
            <SortableTh field="net_owed" sort={pSort.sort} onSort={pSort.toggle} style={th}>Net owed</SortableTh>
            <SortableTh field="due_date" sort={pSort.sort} onSort={pSort.toggle} style={th}>Due</SortableTh>
            <SortableTh field="status" sort={pSort.sort} onSort={pSort.toggle} style={th}>Status</SortableTh>
          </tr></thead>
          <tbody>{pSort.sorted.map((r: any, i: number) => (
            <tr key={i} onClick={() => openDrill(r.imei)} style={{ cursor: 'pointer' }}>
              <td style={td}>{r.imei}</td><td style={td}>{r.store}</td><td style={td}>{r.device_model}</td>
              <td style={td}>{r.owed == null ? '—' : fmt(r.owed)}</td>
              <td style={td}>{r.rebate_amount ? fmt(r.rebate_amount) : '—'}{r.rebate_mismatch && <span title="Processor cross-check mismatch" style={{ color: 'var(--c-fg-dc2626)' }}> ⚠︎</span>}</td>
              <td style={{ ...td, fontWeight: 600 }}>{r.net_owed == null ? '—' : fmt(r.net_owed)}</td>
              <td style={td}>{r.due_date || '—'}</td>
              <td style={td}><span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 11, color: 'var(--c-fg-ffffff)', background: STATUS_COLORS[r.status] || 'var(--c-bg-6b7280)' }}>{r.status}</span>{r.priority && <span title="Priority sell (final 25% of window)"> 🔥</span>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}

      {tab === 'owed' && !loading && (
        oRows.length === 0 ? (
          // HONEST EMPTY (owner report 2026-08-10: "Daily owed is also not showing, it should show how
          // much is owed"). An empty table here is almost never "you owe nothing" — it is "no device
          // carries an amount", which happens when the tenant's payable source map has no owed field
          // (every Total/MA map: the MA reports say what was ACTIVATED, never what was INVOICED). Say
          // that, and point at the one thing that fixes it, instead of rendering a blank grid.
          <div className="card" style={{ padding: 20, fontSize: 13, color: 'var(--text2)', maxWidth: 820 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>No dated amounts to show yet.</div>
            This tab groups <b>net owed</b> by due date, so it is empty whenever no device carries an
            amount. That is the case when the carrier&apos;s source map has no <b>owed</b> field — the
            Total / marketplace reports record what was <i>activated</i>, never what was <i>invoiced</i>,
            so there is no price and no due date to group by. Loading the <b>vendor invoices</b> is what
            supplies owed + invoice date + terms; until then every sold device with no rebate lands in{' '}
            <button onClick={() => { setTab('payables'); setStatus('discrepancy') }}
              style={{ background: 'none', border: 'none', color: 'var(--c-fg-dc2626)', cursor: 'pointer', fontSize: 13, padding: 0, textDecoration: 'underline' }}>
              Payables → Discrepancy
            </button>{' '}at $0.
          </div>
        ) : (
          <>
          {/* GRAIN + SOURCE, said out loud (owner 2026-08-10 "if we have the actual due date build that
              and ship it"). When the amounts come from the processor's own feed rather than the
              per-IMEI ledger, the rows are ORDER LINES and there is no device to drill into — the
              reader has to know that before comparing this to the Payables tab. */}
          {(oMeta.unassigned_owed ?? 0) > 0 && (
            <div style={{ fontSize: 12, color: 'var(--c-fg-d97706)', marginBottom: 10 }}>
              <b>{fmt(oMeta.unassigned_owed)}</b> of this is on devices whose store could not be resolved,
              so it cannot be debited to an account yet. It is shown on its own line inside each date
              rather than added to another store.
            </div>
          )}
          {oMeta.source === 'vendor_feed' && oMeta.note && (
            <div style={{ fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 11px', marginBottom: 10, maxWidth: 980 }}>
              ℹ️ {oMeta.note}
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <SortableTh field="due_date" sort={oSort.sort} onSort={oSort.toggle} style={th}>Due date</SortableTh>
              <SortableTh field="count" sort={oSort.sort} onSort={oSort.toggle} style={th}>{oMeta.grain === 'order_line' ? 'Order lines' : 'Devices'}</SortableTh>
              <SortableTh field="owed" sort={oSort.sort} onSort={oSort.toggle} style={th}>Owed</SortableTh>
            </tr></thead>
            {/* WHO OWES WHAT ON EACH DATE (owner 2026-10-26: "who owes what for eveyr date mentioned
                on that list , to make sure thee correct account is debited"). A date total cannot
                answer that, so each date opens into its stores — the split comes down WITH the date
                from one request, so it is always the same money the date row totals. */}
            <tbody>{oSort.sorted.map((r: any, i: number) => (
              <Fragment key={i}>
                <tr onClick={() => setOpenDate(d => d === r.due_date ? '' : r.due_date)}
                  style={{ cursor: (r.by_store || []).length ? 'pointer' : 'default' }}>
                  <td style={td}>{(r.by_store || []).length ? (openDate === r.due_date ? '▾ ' : '▸ ') : ''}{r.due_date}</td>
                  <td style={td}>{r.count}{r.stores ? ` · ${r.stores} store${r.stores === 1 ? '' : 's'}` : ''}</td>
                  <td style={{ ...td, fontWeight: 600 }}>{fmt(r.owed)}</td>
                </tr>
                {openDate === r.due_date && ((r.by_store || []) as OwedByStore[]).map((b, j) => (
                  <tr key={`${i}-${j}`} style={{ background: 'var(--surface2)' }}>
                    <td style={{ ...td, paddingLeft: 28 }}>
                      {b.store || <i>store not resolved — cannot be debited to an account</i>}
                      {b.market && <span style={{ color: 'var(--muted)' }}> · {b.market}</span>}
                    </td>
                    <td style={td}>{b.count}</td>
                    <td style={{ ...td, fontWeight: 600 }}>{fmt(b.owed)}</td>
                  </tr>
                ))}
              </Fragment>
            ))}</tbody>
            <tfoot><tr>
              <td style={{ ...td, fontWeight: 800 }}>TOTAL</td>
              <td style={td} />
              <td style={{ ...td, fontWeight: 800 }}>{fmt(oMeta.total_owed ?? 0)}</td>
            </tr></tfoot>
          </table>
          </>
        )
      )}

      {tab === 'terms' && !loading && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14, maxWidth: 820, lineHeight: 1.6 }}>
            How many days after a device ships you have to pay the distributor for it. Change the number and
            save — the upcoming due dates recalculate themselves from the new terms, so there is nothing else
            to press. Leave it <b>blank</b> for a carrier that stamps a real due date on every line of its own
            statement: a blank means &quot;the carrier tells us&quot;, which is not the same as 0 days.
          </div>
          {terms.length === 0
            ? <div style={{ color: 'var(--muted)', fontSize: 13 }}>No carrier is declared for this tenant yet.</div>
            : <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 820 }}>
                <thead><tr>
                  <th style={th}>Carrier</th>
                  <th style={th}>Net payment terms</th>
                  <th style={th}>Where the due date comes from</th>
                  <th style={th}></th>
                </tr></thead>
                <tbody>{terms.map((r: TermsRow) => {
                  const key = String(r.carrier_id)
                  // THE BOX SHOWS THE SAVED VALUE until it is touched, so a row nobody edited cannot be
                  // saved back as something else. `?? ''` and not `|| ''`: 0 days is a real declaration.
                  const shown = key in termsDraft ? termsDraft[key] : (r.net_terms_days ?? '') === '' ? '' : String(r.net_terms_days)
                  const dirty = key in termsDraft && termsDraft[key] !== ((r.net_terms_days ?? '') === '' ? '' : String(r.net_terms_days))
                  return (
                    <tr key={key}>
                      <td style={td}>{r.label || key}</td>
                      <td style={td}>
                        <input value={shown} inputMode="numeric" placeholder="not declared"
                          onChange={e => setTermsDraft(d => ({ ...d, [key]: e.target.value }))}
                          style={{ width: 92, padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
                        <span style={{ color: 'var(--muted)', fontSize: 12, marginLeft: 6 }}>days</span>
                      </td>
                      <td style={{ ...td, fontSize: 12, color: 'var(--text2)' }}>
                        {r.due_date_mode === 'field'
                          ? <>the carrier&apos;s own date on each line{r.due_date_field ? <> (<code>{r.due_date_field}</code>)</> : null}</>
                          : <>counted from the ship date using these terms</>}
                      </td>
                      <td style={td}>
                        <button disabled={busy || !dirty} onClick={() => saveTerms(key, shown)}
                          style={{ padding: '5px 13px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: dirty && !busy ? 'pointer' : 'default',
                            /* TOKENS ONLY, no literal and no `var(--x, #hex)` fallback. The tab buttons
                               above still carry a literal inside a var() fallback and the §B1 theme lock
                               does not see it; it saw these two, which is the lock being right about the
                               newer code rather than the older. A hex here would also not come down for
                               the dark theme, since only a token is redefined under [data-theme="dark"]. */
                            background: dirty && !busy ? 'var(--accent)' : 'var(--surface)',
                            color: dirty && !busy ? 'var(--c-fg-ffffff)' : 'var(--muted)' }}>
                          Save
                        </button>
                      </td>
                    </tr>
                  )
                })}</tbody>
              </table>}
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 14, maxWidth: 820 }}>
            A carrier that declares nothing here falls back to its distributor&apos;s own terms, and if that is
            unset too the line carries no due date at all rather than a guessed one.
          </div>
        </div>
      )}

      {tab === 'map' && !loading && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
            Map each raw model name (as it appears in sales or inventory) to a canonical model + its carrier, so the forecast can line sales up with stock and split by carrier. This is an onboarding to-do — map the highest-frequency ones first.
          </div>
          <h3 style={{ fontSize: 15, margin: '0 0 8px' }}>Unmapped models ({candidates.length})</h3>
          {candidates.length === 0
            ? <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 20 }}>All seen models are mapped. 🎉</div>
            : <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 24 }}>
                <thead><tr><th style={th}>Raw model (side · freq)</th><th style={th}>Canonical model</th><th style={th}>Carrier</th><th style={th}></th></tr></thead>
                <tbody>{candidates.map((c, i) => <MapRow key={c.raw_model + i} cand={c} carriers={carriersList} onSave={saveMapping} />)}</tbody>
              </table>}
          <h3 style={{ fontSize: 15, margin: '0 0 8px' }}>Existing mappings ({mappings.length})</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Raw model</th><th style={th}>Canonical</th><th style={th}>Carrier</th><th style={th}></th></tr></thead>
            <tbody>{mappings.map((m: any) => (
              <tr key={m.id}><td style={td}>{m.raw_model}</td><td style={td}>{m.canonical_model}</td><td style={td}>{m.carrier_name || '—'}</td>
                <td style={td}><button onClick={() => deleteMapping(m.id)} style={{ background: 'none', border: 'none', color: 'var(--c-fg-dc2626)', cursor: 'pointer', fontSize: 12 }}>Remove</button></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {drill && (
        <div onClick={() => setDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', borderRadius: 12, padding: 24, maxWidth: 560, width: '90%', maxHeight: '80vh', overflow: 'auto' }}>
            <h3 style={{ margin: '0 0 4px' }}>What offsets what — {drill.device_model}</h3>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>IMEI {drill.imei} · {drill.store} · {drill.status}{drill.sold ? ` · sold ${drill.sold_date || ''}` : ' · unsold'}</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <tbody>
                <tr><td style={td}>Owed to vendor</td><td style={{ ...td, textAlign: 'right' }}>{drill.owed == null ? `— (${owedSourceLabel(drill.owed_source)})` : fmt(drill.owed)}</td></tr>
                <tr><td style={td}>Primary rebate ({drill.primary_rebate?.source})</td><td style={{ ...td, textAlign: 'right', color: 'var(--c-fg-16a34a)' }}>{drill.primary_rebate?.amount ? '− ' + fmt(drill.primary_rebate.amount) : '—'}</td></tr>
                {(drill.epay_crosscheck?.lines || []).map((l: any, i: number) => (
                  <tr key={i}><td style={td}>Processor: {l.type} ({l.date})</td><td style={{ ...td, textAlign: 'right', color: 'var(--muted)' }}>{fmt(l.amount)}</td></tr>
                ))}
                {drill.epay_crosscheck?.mismatch && <tr><td style={td} colSpan={2}><span style={{ color: 'var(--c-fg-dc2626)' }}>⚠︎ Processor cross-check ({fmt(drill.epay_crosscheck.amount || 0)}) disagrees with the primary rebate</span></td></tr>}
                <tr><td style={{ ...td, fontWeight: 700 }}>Net owed</td><td style={{ ...td, textAlign: 'right', fontWeight: 700 }}>{drill.net_owed == null ? '—' : fmt(drill.net_owed)}</td></tr>
              </tbody>
            </table>
            <button onClick={() => setDrill(null)} style={{ marginTop: 16, padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
