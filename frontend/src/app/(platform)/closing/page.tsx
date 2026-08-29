'use client'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import type { EntityOption } from '@/components/EntityPicker'
import type { StandardFilterValue } from '@/lib/standard-filters'
import ReportShell from '@/components/ReportShell'
import type { ExportColumn } from '@/lib/export'
import SubmissionsTable, { monthStart } from './_lib/SubmissionsTable'
import { MarketStorePicker, type StoreOpt } from './_lib/MarketStorePicker'

const csv = (a: string[]) => (a.length ? a.join(',') : undefined)

export default function ClosingDashboard() {
  const { user, permissions } = useAuth()
  // RULE FIVE (§3d), OWNER DIRECTIVE 2026-07-28 (same-day follow-up): ONE standardized filter bar
  // (date-range + store(s)/market(s)/rep(s)) drives the tiles AND all three tabs — By-store/By-rep
  // used to have only a month + market select while "All submissions" already had the full bar; that
  // asymmetry is fixed by lifting the filter state here and passing it down into <SubmissionsTable>.
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({ period: monthStart(), periodTo: localToday(), stores: [], markets: [], reps: [] }))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'detail' | 'store' | 'rep'>('detail')
  const [readiness, setReadiness] = useState<any>(null)   // self-diagnostic (2026-07-16), best-effort
  // Anti-clobber: only the LATEST in-flight rollup request may land (a fast filter change used to let
  // a slower, stale response overwrite a newer one — the timeclock last-response-wins race class).
  const reqRef = useRef(0)

  // retail-ops-25 (PACKAGE B, OWNER DIRECTIVE 2026-08-03 "one tile for cash short and one tile for cash
  // over ... drill down behind every tile"): DESIGN CHOICE — reuse the module's OWN existing precedent
  // (the shared `filt` state already driving all 3 tabs, retail-ops-14) rather than new per-tile pages
  // (the asset-charges-dashboard style). Every top tile's underlying rows are literally the SAME
  // row-level `daily_closing` submissions the "All submissions" tab (<SubmissionsTable>, RULE FIVE bar
  // + RULE FOUR ReportShell exports already wired) already shows for the identical filt scope — so a
  // tile click just (a) switches to that tab and (b) for the 2 new tiles, narrows it via `drill` to
  // exactly the rows behind that tile (cash_short_amount/cash_over_amount > 0, the SAME structured
  // fields `/closing/submissions` now returns from the canonical `_money_issues` call — never
  // re-derived here). This is a true drill-down (aggregate tile -> the row-level detail composing it),
  // arrives pre-filtered (same shared `filt`, no navigation/deep-link needed since it's the same page),
  // and the filter bar stays visible so the user can widen — without building 12 near-duplicate pages.
  // <SubmissionsTable> is ALWAYS mounted (visually hidden via its `hidden` prop when tab !== 'detail')
  // so its fetch keeps the 2 new tiles' totals live no matter which tab is currently showing.
  const [drill, setDrill] = useState<'all' | 'cash_short' | 'cash_over'>('all')
  const [subRows, setSubRows] = useState<any[]>([])
  const [subMeta, setSubMeta] = useState<any>(null)
  const onScopedRows = useCallback((rows: any[], meta: any) => { setSubRows(rows); setSubMeta(meta) }, [])
  const drillTo = (d: 'all' | 'cash_short' | 'cash_over') => { setTab('detail'); setDrill(d) }
  const cashShortTotal = useMemo(() => Math.round(subRows.reduce((s, r) => s + (r.cash_short_amount || 0), 0) * 100) / 100, [subRows])
  const cashOverTotal = useMemo(() => Math.round(subRows.reduce((s, r) => s + (r.cash_over_amount || 0), 0) * 100) / 100, [subRows])
  // Same money-secrecy boundary the detail tab already shows a note for — undefined (meta not loaded
  // yet) reads as "unknown", not a false negative; only an explicit `false` hides the real $ amounts.
  const canReviewMoney = subMeta?.can_review !== false

  useEffect(() => {
    const mkt = user?.market
    if (mkt && permissions?.scope === 'market') setFilt(f => (f.markets.length ? f : { ...f, markets: [mkt] }))
  }, [user, permissions])

  // Canonical, org-scoped option sources (pick-don't-type §3b) — NEVER derived from the loaded rollup
  // response (the original bug: an empty/filtered rollup meant empty market options, a circular trap).
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP).then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): the same roster, shaped for
  // <MarketStorePicker> (needs each store's own `.market` to cascade against).
  const storesForCascade: StoreOpt[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [pStores])
  const marketOptions: EntityOption[] = useMemo(() => {
    const real = Array.from(new Set(pStores.map((s: any) => s.market).filter(Boolean))).sort()
    return [...real.map((m: string) => ({ id: m, label: m })), { id: '(no market)', label: '(no market)' }]
  }, [pStores])
  const repOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name, sublabel: e.email || undefined })),
    [pEmps])

  const load = useCallback(() => {
    if (!filt.period) return
    const myReq = ++reqRef.current
    setLoading(true)
    const qs = new URLSearchParams()
    qs.set('date_from', filt.period)
    qs.set('date_to', filt.periodTo || filt.period)
    const s = csv(filt.stores); if (s) qs.set('stores', s)
    const m = csv(filt.markets); if (m) qs.set('markets', m)
    const r = csv(filt.reps); if (r) qs.set('reps', r)
    api(`/api/v1/closing/rollup?${qs.toString()}`)
      .then(d => { if (reqRef.current === myReq) setData(d) })
      .catch(console.error)
      .finally(() => { if (reqRef.current === myReq) setLoading(false) })
  }, [filt.period, filt.periodTo, filt.stores, filt.markets, filt.reps])
  useEffect(() => { load() }, [load])
  // Surface config/data gaps (no stores mapped, no B2B sales source, no X-report ever, module not
  // entitled) right on the dashboard instead of letting empty tiles/recon speak for themselves.
  useEffect(() => { api('/api/v1/closing/readiness').then(setReadiness).catch(() => {}) }, [])

  const t = data?.totals || {}
  const byStore: any[] = data?.by_store || []
  const byRep: any[] = data?.by_rep || []
  const cashTotal = (r: any) => (r.store_cash || 0) + (r.epay_cash || 0)
  const cardTotal = (r: any) => (r.store_cc || 0) + (r.epay_cc || 0)
  // retail-ops-22 (OWNER DIRECTIVE 2026-08-03 "Daily Closing dashboard should also show the epay bill
  // payments"): `epay_cash`/`epay_cc` above are the LEGACY columns folded into cashTotal/cardTotal —
  // create_row always zeroes them for a modern (mig103+) row, so they alone would show $0 forever.
  // `epay_on_cash`/`epay_on_cc` (GET /closing/rollup, no new backend read — same daily_closing rows
  // already loaded, just aggregated with the era-aware helper `/closing/summary`'s DM Verify page
  // already proved correct) are the REAL figure for both eras. These are a SUBSET of cash/credit
  // already counted above — display-only, never added into cashTotal/cardTotal or any other total.
  const epayCash = (r: any) => r.epay_on_cash || 0
  const epayCard = (r: any) => r.epay_on_cc || 0

  const cov = data ? `${data.verified_keys}/${data.submitted_keys}` : '—'

  const storeColumns: ExportColumn[] = useMemo(() => [
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address || r.store_name || '—' },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Days', field: 'days', type: 'number', get: (r: any) => r.days },
    { header: 'Cash $', field: 'cash', money: true, get: (r: any) => cashTotal(r) },
    { header: 'Credit $', field: 'credit', money: true, get: (r: any) => cardTotal(r) },
    { header: 'ePay Cash $', field: 'epay_cash', money: true, get: (r: any) => epayCash(r) },
    { header: 'ePay Credit $', field: 'epay_credit', money: true, get: (r: any) => epayCard(r) },
    { header: 'Accessory $', field: 'acc_sale', money: true, get: (r: any) => r.acc_sale },
    { header: 'Other $', field: 'other_account', money: true, get: (r: any) => r.other_account },
    { header: 'Upgrades #', field: 'upgrade_count', type: 'number', get: (r: any) => r.upgrade_count },
    { header: 'New Lines #', field: 'new_line_count', type: 'number', get: (r: any) => r.new_line_count },
    { header: 'Postpaid #', field: 'postpaid_count', type: 'number', get: (r: any) => r.postpaid_count },
    { header: 'Submissions #', field: 'rows', type: 'number', get: (r: any) => r.rows },
  ], [])

  const repColumns: ExportColumn[] = useMemo(() => [
    { header: 'Rep', field: 'employee_name', role: 'rep', get: (r: any) => r.employee_name || '—' },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: any) => r.store_address || '—' },
    { header: 'Market', field: 'market', get: (r: any) => r.market },
    { header: 'Days', field: 'days', type: 'number', get: (r: any) => r.days },
    { header: 'Cash $', field: 'cash', money: true, get: (r: any) => cashTotal(r) },
    { header: 'Credit $', field: 'credit', money: true, get: (r: any) => cardTotal(r) },
    { header: 'ePay Cash $', field: 'epay_cash', money: true, get: (r: any) => epayCash(r) },
    { header: 'ePay Credit $', field: 'epay_credit', money: true, get: (r: any) => epayCard(r) },
    { header: 'Accessory $', field: 'acc_sale', money: true, get: (r: any) => r.acc_sale },
    { header: 'Other $', field: 'other_account', money: true, get: (r: any) => r.other_account },
    { header: 'Upgrades #', field: 'upgrade_count', type: 'number', get: (r: any) => r.upgrade_count },
    { header: 'New Lines #', field: 'new_line_count', type: 'number', get: (r: any) => r.new_line_count },
    { header: 'Postpaid #', field: 'postpaid_count', type: 'number', get: (r: any) => r.postpaid_count },
    { header: 'Submissions #', field: 'rows', type: 'number', get: (r: any) => r.rows },
  ], [])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Daily Closing</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Closing summaries by store and by rep, with DM verification coverage.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link href="/closing/submit" className="btn btn-primary" style={{ fontSize: 13 }}>➕ Submit closing</Link>
          <Link href="/closing/verify" className="btn btn-secondary" style={{ fontSize: 13 }}>✅ DM verify</Link>
          <Link href="/closing/cash-position" className="btn btn-secondary" style={{ fontSize: 13 }}>💰 Cash Position</Link>
          <Link href="/closing/store-cash-on-hand" className="btn btn-secondary" style={{ fontSize: 13 }}>🏦 Store Cash on Hand</Link>
          <Link href="/closing/duplicates" className="btn btn-secondary" style={{ fontSize: 13 }}>🧾 Duplicates</Link>
          <Link href="/closing/envelope-payout" className="btn btn-secondary" style={{ fontSize: 13 }}>✉️ Envelope Payout</Link>
          <Link href="/closing/expenses-report" className="btn btn-secondary" style={{ fontSize: 13 }}>🧾 Expenses Report</Link>
          <Link href="/closing/expense-categories" className="btn btn-secondary" style={{ fontSize: 13 }}>🗂️ Expense Categories</Link>
          <Link href="/closing/deposit-recon" className="btn btn-secondary" style={{ fontSize: 13 }}>💵 Cash Deposit Recon</Link>
          <Link href="/closing/deposit-categories" className="btn btn-secondary" style={{ fontSize: 13 }}>🗂️ Deposit Categories</Link>
          <Link href="/closing/envelope-config" className="btn btn-secondary" style={{ fontSize: 13 }}>⚙️ Envelope Config</Link>
          <Link href="/closing/count-config" className="btn btn-secondary" style={{ fontSize: 13 }}>🔢 Count fields</Link>
          <Link href="/closing/readiness" className="btn btn-secondary" style={{ fontSize: 13 }}>🩺 Readiness</Link>
        </div>
      </div>

      {readiness && !readiness.ok && (
        <Link href="/closing/readiness" className="card" style={{ display: 'block', padding: 12, marginBottom: 14, background: '#fdeaea', border: '1px solid #f3b4b4', textDecoration: 'none', color: 'inherit' }}>
          🔴 <b>{readiness.issues.filter((i: any) => i.severity === 'critical').length} critical wiring gap(s)</b> found for this tenant — {readiness.issues.find((i: any) => i.severity === 'critical')?.message} <span style={{ textDecoration: 'underline' }}>See all →</span>
        </Link>
      )}

      {/* Filters — RULE FIVE core set (date-range + store(s)/market(s)/rep(s)), canonical org-scoped
          option sources. Drives the tiles + all 3 tabs below (including "All submissions", which no
          longer renders its own competing bar — see SubmissionsTable's filterValue prop). Market/store
          render via the shared cascade-checkbox <MarketStorePicker> (OWNER DIRECTIVE 2026-08-04, appended
          through StandardFilterBar's `right` slot with its own built-in market/store pickers hidden) —
          the underlying `filt.stores`/`filt.markets` string arrays are unchanged, so every downstream
          consumer (tiles, SubmissionsTable, exports) needs no changes. */}
      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        show={{ stores: false, markets: false }}
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
        right={
          <MarketStorePicker
            stores={storesForCascade}
            selectedMarkets={filt.markets} onMarketsChange={ids => setFilt(f => ({ ...f, markets: ids }))}
            selectedStores={filt.stores} onStoresChange={ids => setFilt(f => ({ ...f, stores: ids }))}
            marketPlaceholder="Markets…" storePlaceholder="Stores…" marketWidth={170} storeWidth={200}
          />
        }
      />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {/* Summary tiles — retail-ops-25: EVERY tile drills down into the "All submissions" detail
              tab (row-level, RULE FIVE bar + RULE FOUR exports already wired), pre-filtered to the
              CURRENT dashboard scope (same shared `filt`); Cash Short/Cash Over additionally narrow to
              exactly the rows behind that tile. See the design-choice comment above `drill` state. */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Cash collected" value={fmt(cashTotal(t))} onClick={() => drillTo('all')} />
            <Tile label="Credit collected" value={fmt(cardTotal(t))} onClick={() => drillTo('all')} />
            <Tile label="ePay cash" value={fmt(epayCash(t))} sub="already inside Cash collected" onClick={() => drillTo('all')} />
            <Tile label="ePay credit" value={fmt(epayCard(t))} sub="already inside Credit collected" onClick={() => drillTo('all')} />
            <Tile label="Accessory sales" value={fmt(t.acc_sale)} onClick={() => drillTo('all')} />
            <Tile label="Other (Zelle/CashApp)" value={fmt(t.other_account)} onClick={() => drillTo('all')} />
            <Tile label="Activations" value={`${(t.new_line_count || 0) + (t.postpaid_count || 0)}`} sub={`${t.new_line_count || 0} new · ${t.postpaid_count || 0} postpaid`} onClick={() => drillTo('all')} />
            <Tile label="Upgrades" value={`${t.upgrade_count || 0}`} onClick={() => drillTo('all')} />
            <Tile label="Rep submissions" value={`${t.rows || 0}`} sub={`${t.days || 0} day(s)`} onClick={() => drillTo('all')} />
            <Tile label="DM verified" value={cov} sub="store-days verified" onClick={() => drillTo('all')} />
            {/* retail-ops-25 — the 2 NEW tiles (OWNER DIRECTIVE 2026-08-03). Sourced from the SAME
                canonical `_money_issues` figures the close gate itself computes (GET
                /closing/submissions' cash_short_amount/cash_over_amount, never re-derived here) —
                summed but never NETTED against each other (the whole point of two separate tiles). */}
            <Tile label="Cash short" value={canReviewMoney ? fmt(cashShortTotal) : '—'}
                  sub={canReviewMoney ? 'block — vs B2B sales' : 'company-wide roles only'}
                  tone={cashShortTotal > 0 ? '#b42318' : undefined} onClick={() => drillTo('cash_short')} />
            <Tile label="Cash over" value={canReviewMoney ? fmt(cashOverTotal) : '—'}
                  sub={canReviewMoney ? 'flag — investigate' : 'company-wide roles only'}
                  tone={cashOverTotal > 0 ? '#b45309' : undefined} onClick={() => drillTo('cash_over')} />
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            {(['detail', 'store', 'rep'] as const).map(x => (
              <button key={x} className={`btn ${tab === x ? 'btn-primary' : 'btn-secondary'}`} style={{ fontSize: 13 }} onClick={() => { setTab(x); if (x === 'detail') setDrill('all') }}>
                {x === 'detail' ? '🧾 All submissions' : x === 'store' ? '🏬 By store' : '🧑 By rep'}
              </button>
            ))}
          </div>

          {/* retail-ops-25: <SubmissionsTable> stays ALWAYS mounted (visually hidden via its own
              `hidden` prop, not unmounted) so its fetch keeps reporting scoped rows up via
              `onScopedRows` for the 2 new tiles' totals no matter which tab is currently showing. */}
          <SubmissionsTable filterValue={filt} onFilterChange={setFilt}
            storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions}
            onScopedRows={onScopedRows} drill={drill} onClearDrill={() => setDrill('all')}
            hidden={tab !== 'detail'} />

          {tab === 'detail' ? null : tab === 'store' ? (
            byStore.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No closing rows for this range yet.</div>
            ) : (
              <ReportShell title="Daily Closing — By Store" subtitle={`${filt.period} → ${filt.periodTo}`}
                filename={`daily-closing-by-store_${filt.period}_${filt.periodTo}`}
                columns={storeColumns} rows={byStore} stickyHeader totals />
            )
          ) : (
            byRep.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No rep submissions for this range yet.</div>
            ) : (
              <ReportShell title="Daily Closing — By Rep" subtitle={`${filt.period} → ${filt.periodTo}`}
                filename={`daily-closing-by-rep_${filt.period}_${filt.periodTo}`}
                columns={repColumns} rows={byRep} stickyHeader totals />
            )
          )}
        </>
      )}
    </div>
  )
}

// retail-ops-25 (PACKAGE B): every tile is now clickable — a drill-down into the row-level detail
// backing it (see the `drillTo` design-choice comment above). `onClick` is optional so this component
// stays usable as a plain display tile elsewhere with no behavior change.
const Tile = ({ label, value, sub, tone, onClick }: { label: string; value: string; sub?: string; tone?: string; onClick?: () => void }) => (
  <div className="card" style={{ padding: 14, cursor: onClick ? 'pointer' : undefined }}
       onClick={onClick} role={onClick ? 'button' : undefined} title={onClick ? 'Click to see the underlying rows' : undefined}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: tone }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
