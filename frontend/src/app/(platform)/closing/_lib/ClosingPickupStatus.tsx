'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportShell from '@/components/ReportShell'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'
import type { ExportColumn } from '@/lib/export'

// WHO DID NOT CLOSE, AND WHOSE CASH IS STILL IN THE DRAWER (owner ask 2026-10-14), verbatim:
// "On the daily closing dashboard and daily closing page I need report which store did not submit
// closing and which store got store cash picked and another report which cash did not get [picked
// up] - these can be in one report with 2 filters added picked and not picked up - with all standard
// filters".
//
// IT IS ONE REPORT, AND IT IS NOT A NEW ONE. Every number here comes from
// GET /closing/accountability-chain — the five-stage chain that already reports, per store and day,
// whether the closing was filed and whether the cash was collected. This screen is a VIEW of it with
// the two columns the owner asked for brought to the front; it runs no query and derives no figure of
// its own, so it can never disagree with the chain, the cash-pickup screen or the accountability
// board about one store-day. Building a second endpoint for the same question is the duplicate defect
// the house rules forbid.
//
// THE WORDS ARE THE SERVER'S. Every state label, and the meaning of each filter, come down in the
// response (`summary.closing_states` / `summary.pickup_states`, and each row's own `closing_label` /
// `pickup_label`), so this file spells no state key and no state label of its own — rename one in the
// pure module and this screen follows.
const NO_MARKET = '(no market)'
const csv = (a: string[]) => (a.length ? a.join(',') : undefined)
const ymd = (s?: string) => (s || '').slice(0, 10)
const stamp = (s?: string) => (s || '').slice(0, 16).replace('T', ' ')
const money = (n: number | undefined) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)

// ── The server's shapes, named. Typed rather than `any` so a renamed field fails the typecheck
//    instead of rendering a blank column nobody notices.
type StageCell = {
  key: string; label: string; done: boolean; at?: string | null; by?: string[]; detail?: string
  collected_recorded?: number
}
type WorkedPerson = { name?: string }
const names = (people?: WorkedPerson[]) => (people || []).map(p => p?.name).filter(Boolean).join(', ')
type ChainRow = {
  store_code: string; day: string; store_name?: string; market?: string | null
  closing_state: string; closing_label: string; closing_submitted: boolean
  pickup_state: string; pickup_label: string
  cash_declared: number; cash_picked: number; cash_awaiting: number
  stages: StageCell[]
  dm?: { names?: string[]; resolved?: boolean; district?: string | null }
  worked?: { people?: WorkedPerson[]; scheduled_only?: WorkedPerson[]; source?: string; resolved?: boolean }
}
type StateCount = { key: string; label: string; store_days: number }
type ChainResponse = {
  start: string; end: string; days: number; stores_expected: number
  rows: ChainRow[]
  summary: {
    store_days?: number
    closing_states?: StateCount[]
    pickup_states?: StateCount[]
    cash_declared?: number; cash_picked?: number; cash_awaiting?: number
  }
  stages: { key: string; label: string }[]
  filters?: { spine?: boolean }
  worked_resolved_dates?: string[]
  worked_dates_capped?: boolean
}
type StoreLookup = { store_code?: string; store_address?: string; market?: string | null }
type EmployeeLookup = { name?: string; email?: string | null }

// The two filters the owner asked for, as a reader sees them. The VALUES are the server's
// (`pickup` / `closing` query values); the label beside each says what that choice admits, so nobody
// has to guess whether a half-collected day counts as collected.
const PICKUP_CHOICES = [
  { value: '', label: 'Cash pickup: all' },
  { value: 'not_picked', label: 'Cash NOT picked up' },
  { value: 'picked', label: 'Cash picked up' },
]
const CLOSING_CHOICES = [
  { value: '', label: 'Closing: all' },
  { value: 'not_submitted', label: 'Closing NOT submitted' },
  { value: 'submitted', label: 'Closing submitted' },
]

export type ClosingPickupStatusProps = {
  /** Shared filter state. The Daily Closing dashboard passes its own so ONE bar drives its tabs; the
   *  standalone page lets this component own it. */
  filterValue?: StandardFilterValue
  onFilterChange?: (v: StandardFilterValue) => void
  /** The dashboard already renders a filter bar above its tabs, so it hides this one. */
  showFilterBar?: boolean
}

export default function ClosingPickupStatus({ filterValue, onFilterChange, showFilterBar = true }: ClosingPickupStatusProps) {
  const today = localToday()
  const [own, setOwn] = useState<StandardFilterValue>({ period: today, periodTo: today, stores: [], markets: [], reps: [] })
  const filt = filterValue || own
  const setFilt = onFilterChange || setOwn

  const [pickup, setPickup] = useState('')
  const [closing, setClosing] = useState('')

  const [pStores, setPStores] = useState<StoreLookup[]>([])
  const [pEmps, setPEmps] = useState<EmployeeLookup[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP)
      .then((s: StoreLookup[]) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP)
      .then((r: EmployeeLookup[] | { employees?: EmployeeLookup[] }) =>
        setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter(s => s.store_code).map(s => ({ id: s.store_code as string, label: s.store_address || (s.store_code as string), sublabel: s.market || undefined })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map(s => s.market).filter(Boolean))) as string[]
    return [...real.sort().map(m => ({ id: m, label: m })), { id: NO_MARKET, label: NO_MARKET }]
  }, [pStores])
  const repOptions: EntityOption[] = useMemo(
    () => pEmps.filter(e => (e.name || '').trim()).map(e => ({ id: e.name as string, label: e.name as string, sublabel: e.email || undefined })),
    [pEmps])

  // THE REQUEST IS A VALUE, and `loading` is DERIVED from whether the answer in hand is the answer to
  // it. That is why there is no `setLoading` here: a spinner driven by a setState inside the effect
  // cascades a render on every filter keystroke, and the derived form cannot get out of step with the
  // request it describes.
  const query = useMemo(() => {
    const from = filt.period || today
    const qs = new URLSearchParams()
    qs.set('start', from)
    qs.set('end', filt.periodTo || from)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('employees', r)
    if (pickup) qs.set('pickup', pickup)
    if (closing) qs.set('closing', closing)
    return qs.toString()
  }, [filt.period, filt.periodTo, filt.stores, filt.markets, filt.reps, pickup, closing, today])

  const [answer, setAnswer] = useState<{ query: string; data: ChainResponse | null; err: string } | null>(null)
  // Anti-clobber: only the LATEST in-flight request may land, or a fast filter change lets a slower,
  // stale response overwrite a newer one (the last-response-wins race this module has hit before).
  const reqRef = useRef(0)
  useEffect(() => {
    const myReq = ++reqRef.current
    api(`/api/v1/closing/accountability-chain?${query}`)
      .then((d: ChainResponse) => { if (reqRef.current === myReq) setAnswer({ query, data: d, err: '' }) })
      .catch((e: Error) => { if (reqRef.current === myReq) setAnswer({ query, data: null, err: e?.message || String(e) }) })
  }, [query])
  const loading = !answer || answer.query !== query
  const data = answer && answer.query === query ? answer.data : null
  const err = answer && answer.query === query ? answer.err : ''

  const rows: ChainRow[] = data?.rows || []
  const summary = data?.summary || {}
  const closingStates: StateCount[] = summary.closing_states || []
  const pickupStates: StateCount[] = summary.pickup_states || []

  // A stage cell is read by its KEY, never by a hardcoded position, so a re-ordered chain cannot
  // silently point these columns at another stage.
  const cell = useCallback((r: ChainRow, key: string): StageCell | Record<string, never> => {
    const i = (data?.stages || []).findIndex(s => s.key === key)
    return i >= 0 ? ((r.stages || [])[i] || {}) : {}
  }, [data])

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'day', type: 'date', role: 'date', get: (r: ChainRow) => ymd(r.day),
      tip: 'The store-day this line is about. Every active store appears for every day in the range, so a store that filed nothing is listed rather than left out.' },
    { header: 'Store', field: 'store_name', role: 'store', get: (r: ChainRow) => r.store_name || r.store_code,
      tip: 'The store, by its address.' },
    { header: 'Market', field: 'market', get: (r: ChainRow) => r.market || '',
      tip: 'The market the store belongs to.' },
    { header: 'Closing', field: 'closing_label', get: (r: ChainRow) => r.closing_label || '',
      tip: 'Whether the daily closing was submitted. "Entered, awaiting correction" means a rep DID submit and the close gate sent them back to recount — a correction to chase, not a missing closing.' },
    { header: 'Closed by', field: 'closed_by', get: (r: ChainRow) => (cell(r, 'closing').by || []).join(', '),
      tip: 'Who submitted the closing.' },
    { header: 'Closed at', field: 'closed_at', get: (r: ChainRow) => stamp(cell(r, 'closing').at || ''),
      tip: 'When the last rep of that store-day submitted.' },
    // WHO WORKED and WHO WAS EXPECTED are two columns (owner report 2026-10-14: *"it should only
    // show the actual person who worked for all reporting not the one with the home store"*). This
    // column holds ONLY an actual signal — a punch or a sale, one row per person however many ways
    // the two feeds spell them. The roster's guess has its own column and says so in its heading.
    { header: 'Who worked', field: 'worked', get: (r: ChainRow) => (r.worked?.resolved ? names(r.worked?.people) : 'not resolved'),
      tip: 'Who was actually in the store that day — clocked in or ringing sales — even when nobody closed. One line per person: the punch and the sales feed often spell the same person differently, and they are matched, not listed twice. Blank means nobody clocked in or rang a sale; "not resolved" means this day was not looked up.' },
    { header: 'Scheduled (nobody worked)', field: 'scheduled_only', get: (r: ChainRow) => names(r.worked?.scheduled_only),
      tip: 'Only filled in when NOBODY clocked in or rang a sale: who the schedule expected that day. It is not a record of who was in the store, which is why it is never shown in the "Who worked" column.' },
    { header: 'DM', field: 'dm', get: (r: ChainRow) => (r.dm?.resolved ? (r.dm?.names || []).join(', ') : 'not in org tree'),
      tip: 'The district manager the store is assigned to, from the org chart. "not in org tree" means the store is not wired into the hierarchy.' },
    { header: 'Cash pickup', field: 'pickup_label', get: (r: ChainRow) => r.pickup_label || '',
      tip: 'Whether the store cash was collected. "Partly picked up" means at least one rep envelope of that store-day is still in the drawer, so it answers the "not picked up" filter.' },
    { header: 'Cash declared $', field: 'cash_declared', money: true, get: (r: ChainRow) => r.cash_declared ?? 0,
      tip: 'What the reps declared for that store-day, after approved expenses and withdrawals already taken out of the envelope.' },
    { header: 'Cash picked up $', field: 'cash_picked', money: true, get: (r: ChainRow) => r.cash_picked ?? 0,
      tip: 'The declared cash of the envelopes that were collected.' },
    { header: 'Cash still in store $', field: 'cash_awaiting', money: true, get: (r: ChainRow) => r.cash_awaiting ?? 0,
      tip: 'The declared cash of the envelopes nobody has collected yet. This is the money to chase.' },
    { header: 'Picked up by', field: 'picked_by', get: (r: ChainRow) => (cell(r, 'pickup').by || []).join(', '),
      tip: 'Who collected the envelope(s).' },
    { header: 'Picked up at', field: 'picked_at', get: (r: ChainRow) => stamp(cell(r, 'pickup').at || ''),
      tip: 'When the last envelope of that store-day was collected.' },
    { header: 'Recorded at pickup $', field: 'collected_recorded', money: true, get: (r: ChainRow) => cell(r, 'pickup').collected_recorded ?? 0,
      tip: "What the pickup rows themselves carry. It can differ from the declared figure by design (the tenant's bill-payment netting basis), so it is shown beside it and never subtracted from it." },
  ], [cell])

  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const tile: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 140 }
  const twoFilters = (
    <>
      <select style={sel} value={pickup} onChange={e => setPickup(e.target.value)}
        title="Cash NOT picked up also includes a store-day only partly collected — there is still cash in that drawer.">
        {PICKUP_CHOICES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
      <select style={sel} value={closing} onChange={e => setClosing(e.target.value)}
        title="Whether the daily closing was submitted for that store-day.">
        {CLOSING_CHOICES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    </>
  )

  return (
    <div>
      {showFilterBar ? (
        <StandardFilterBar
          value={filt} onChange={setFilt}
          periodMode="range"
          storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
          storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
          right={twoFilters}
        />
      ) : (
        /* The dashboard owns the standard bar, so the two report-specific filters still need a home. */
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>{twoFilters}</div>
      )}

      {err && <div style={{ color: 'var(--danger)', margin: '10px 0' }}>{err}</div>}
      {loading && <div style={{ margin: '12px 0', color: 'var(--text2)' }}>Loading…</div>}

      {!loading && data && (
        <>
          {/* Tiles: every closing state and every pickup state that occurs, labelled BY THE SERVER,
              plus the one figure a manager acts on — how much cash is still sitting in stores. */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '12px 0 14px' }}>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Store-days</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{summary.store_days ?? 0}</div>
            </div>
            {[...closingStates, ...pickupStates].filter(s => s.store_days > 0).map(s => (
              <div key={s.key} style={tile}>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{s.label}</div>
                <div style={{ fontSize: 19, fontWeight: 700 }}>{s.store_days}</div>
              </div>
            ))}
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Cash still in stores</div>
              <div style={{ fontSize: 19, fontWeight: 700, color: (summary.cash_awaiting || 0) > 0 ? 'var(--warn)' : undefined }}>
                {money(summary.cash_awaiting)}
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>of {money(summary.cash_declared)} declared</div>
            </div>
          </div>

          <ReportShell
            title="Closing & Cash Pickup Status"
            subtitle={`${filt.period} → ${filt.periodTo || filt.period}`}
            notice={(
              <>
                One line per <b>store and day</b>: whether the <b>daily closing was submitted</b>, and whether the{' '}
                <b>store cash was picked up</b>. Every active store appears for every day in the range, so a store that
                filed nothing is listed rather than left out. <b>Cash NOT picked up</b> includes a store-day only partly
                collected, because cash is still in that drawer. Bill-payment pickups are a separate flow and do not
                count as store cash here.
                {data.filters?.spine === false && (
                  <> Filtered to an employee, so only the store-days that person worked are shown.</>
                )}
              </>
            )}
            noticeTone={(summary.cash_awaiting || 0) > 0 ? 'warn' : 'info'}
            filename={`closing-pickup-status_${filt.period}_${filt.periodTo || filt.period}`}
            columns={columns} rows={rows} stickyHeader totals
            defaultGroupBy="Cash pickup"
          />

          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
            {data.days} day(s) × {data.stores_expected} store(s) expected to file · showing {rows.length} store-day row(s)
            {data.worked_dates_capped && (
              <div style={{ marginTop: 4 }}>
                <b>Who worked</b> is resolved for the {(data.worked_resolved_dates || []).length} most recent day(s) in
                this range; older days read “not resolved” rather than showing an empty day as unstaffed. Narrow the
                range to resolve them.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
