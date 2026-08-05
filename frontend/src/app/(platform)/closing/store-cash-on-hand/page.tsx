'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'
import { resolveStoreCodes } from '../_lib/market-store-cascade'

// Store Cash on Hand — OWNER DIRECTIVE 2026-08-04: "need a daily report to show how much cash is in
// each store at the end of the day added with the other days from the past if not picked by the dm or
// given out". OWNER DIRECTIVE 2026-08-05, verbatim: "Store cash on hand should have the date range."
//
// SEMANTICS (stated here AND on the page, per the dispatch): cash on hand is inherently an AS-OF
// balance, not a sum over a range — "what is sitting in this store right now / as of date X" stays the
// PRIMARY question and the default view (Day mode, unchanged from 2026-08-04: today's declared cash
// minus whatever left the envelope today, plus carry-over from every earlier un-swept day). Range mode
// is a NEW, second view that shows the MOVEMENT that produced a balance over a window: opening balance
// at the range start + cash collected − envelope expenses − pickups/deposits = closing balance at the
// range end — and that closing balance is BYTE-IDENTICAL to the Day-mode figure for `date = range end`
// (same `_cash_position_core` math on the backend; proven in harness_store_cash_on_hand_range.py). Same
// Day/Range toggle pattern as the sibling Cash Position report (`/closing/cash-position`) — RULE FIVE
// says match the existing pattern, not invent a new one.
//
// Reads GET /closing/store-cash-on-hand, which reuses the EXACT same shared core as GET /closing/
// cash-position (`_cash_position_core`) in both modes — this report's totals agree with Cash Position's
// `cash_on_hand` for the same store/date BY CONSTRUCTION. RULE FOUR (ReportShell exports carry whichever
// mode/columns are on screen) + RULE FIVE (market->store cascade + checkbox picker).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function StoreCashOnHandPage() {
  const [rangeMode, setRangeMode] = useState(false)
  const [date, setDate] = useState(() => localToday())
  // Range default: current month-to-date (matches Cash Position's own "today" default in spirit — the
  // most recently useful window — while giving Range mode a non-trivial multi-day default on first load).
  const [rangeStart, setRangeStart] = useState(() => localToday().slice(0, 8) + '01')
  const [rangeEnd, setRangeEnd] = useState(() => localToday())
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [pStores, setPStores] = useState<any[]>([])

  useEffect(() => { api('/api/v1/closing/stores').then((s: any) => setPStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])
  const storesForCascade: StoreOpt[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [pStores])
  const resolvedStores = useMemo(() => resolveStoreCodes(storesForCascade, fMarkets, fStores), [storesForCascade, fMarkets, fStores])

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const qs = [
      rangeMode ? `start=${rangeStart}&end=${rangeEnd}` : `date=${date}`,
      resolvedStores.length && `stores=${encodeURIComponent(resolvedStores.join(','))}`,
    ].filter(Boolean).join('&')
    api(`/api/v1/closing/store-cash-on-hand?${qs}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [rangeMode, date, rangeStart, rangeEnd, resolvedStores])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const t = data?.totals || {}
  const mode = data?.mode || (rangeMode ? 'range' : 'single_day')

  const columns: ExportColumn[] = useMemo(() => {
    const base: ExportColumn[] = [
      { header: 'Store', field: 'store_name', role: 'store', get: (r: any) => r.store_name },
      { header: 'Market', field: 'market', get: (r: any) => r.market || '' },
    ]
    if (mode === 'range') {
      return [
        ...base,
        { header: 'Opening balance', field: 'opening_balance', money: true, get: (r: any) => r.opening_balance },
        { header: 'Cash collected', field: 'cash_collected', money: true, get: (r: any) => r.cash_collected },
        { header: 'Envelope expenses', field: 'envelope_expenses', money: true, get: (r: any) => r.envelope_expenses },
        { header: 'Pickups / deposits', field: 'pickups_deposits', money: true, get: (r: any) => r.pickups_deposits },
        { header: 'Closing balance', field: 'closing_balance', money: true, get: (r: any) => r.closing_balance },
        { header: 'Last pickup at', field: 'last_pickup_at', get: (r: any) => r.last_pickup_at ? new Date(r.last_pickup_at).toLocaleString() : '' },
        { header: 'Last deposited at', field: 'last_deposited_at', get: (r: any) => r.last_deposited_at ? new Date(r.last_deposited_at).toLocaleString() : '' },
      ]
    }
    return [
      ...base,
      { header: "Today's declared cash", field: 'today_declared', money: true, get: (r: any) => r.today_declared },
      { header: "Today's picked up / paid out", field: 'today_taken', money: true, get: (r: any) => r.today_taken },
      { header: 'Carryover from prior days', field: 'carryover_from_prior_days', money: true, get: (r: any) => r.carryover_from_prior_days },
      { header: 'Total cash on hand', field: 'total_cash_on_hand', money: true, get: (r: any) => r.total_cash_on_hand },
      { header: 'Last pickup at', field: 'last_pickup_at', get: (r: any) => r.last_pickup_at ? new Date(r.last_pickup_at).toLocaleString() : '' },
      { header: 'Last deposited at', field: 'last_deposited_at', get: (r: any) => r.last_deposited_at ? new Date(r.last_deposited_at).toLocaleString() : '' },
    ]
  }, [mode])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏦 Store Cash on Hand</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            <strong>Day</strong>: how much cash is physically sitting in each store as of that day — the
            primary question this page answers, and what Cash Pickup uses. <strong>Range</strong>: the
            movement that produced the balance over a window — opening balance, cash collected, minus
            envelope expenses and pickups/deposits, equals the closing balance (identical to Day mode for
            a day equal to the range&apos;s end). Same math as{' '}
            <Link href="/closing/cash-position" style={{ color: 'var(--accent)' }}>Cash Position</Link> in
            both modes.
          </p>
        </div>
        <Link href="/closing/cash-position" className="btn btn-secondary" style={{ fontSize: 13 }}>💰 Cash Position</Link>
      </div>

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 14, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', fontSize: 12, background: !rangeMode ? 'var(--accent)' : 'transparent', color: !rangeMode ? 'white' : 'var(--text2)' }} onClick={() => setRangeMode(false)}>Day</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', fontSize: 12, background: rangeMode ? 'var(--accent)' : 'transparent', color: rangeMode ? 'white' : 'var(--text2)' }} onClick={() => setRangeMode(true)}>Range</button>
          </div>
          {!rangeMode
            ? <input type="date" style={sel} value={date} onChange={e => setDate(e.target.value)} />
            : <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                <input type="date" style={sel} value={rangeStart} onChange={e => setRangeStart(e.target.value)} />
                →<input type="date" style={sel} value={rangeEnd} onChange={e => setRangeEnd(e.target.value)} />
              </span>}
        </div>
        {/* First consumer of the shared market->store cascade + checkbox picker (OWNER DIRECTIVE 2026-08-04). */}
        <MarketStorePicker
          stores={storesForCascade}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        {(fMarkets.length > 0 || fStores.length > 0) && (
          <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 9px' }} onClick={() => { setFMarkets([]); setFStores([]) }}>Clear</button>
        )}
        {data && (
          <span style={{ fontSize: 13, color: 'var(--text2)' }}>
            {t.stores || 0} store(s) · {mode === 'range' ? 'closing balance' : 'total cash on hand'} {fmt((mode === 'range' ? t.closing_balance : t.total_cash_on_hand) || 0)}
          </span>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : (
        <ReportShell title="Store Cash on Hand"
          subtitle={rangeMode ? `${rangeStart} → ${rangeEnd}` : date}
          filename={`store-cash-on-hand_${rangeMode ? `${rangeStart}_${rangeEnd}` : date}`}
          columns={columns} rows={rows} stickyHeader totals />
      )}
      {data?.opening_note && <p style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>ℹ️ {data.opening_note}</p>}
    </div>
  )
}
