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
// given out". Per store, for the chosen day: TODAY's declared cash minus whatever left the envelope
// today (pickup / bank deposit / EEP withdrawal), PLUS the carry-over balance from every earlier day
// still un-swept. Reads GET /closing/store-cash-on-hand, which reuses the EXACT same shared core as
// GET /closing/cash-position (`_cash_position_core`) — this report's total agrees with Cash Position's
// `cash_on_hand` for the same store/date BY CONSTRUCTION (see harness_store_cash_on_hand.py), it just
// spells out the today-vs-carryover split the owner explicitly asked for. RULE FOUR (ReportShell
// exports) + RULE FIVE (this is the first page built directly against the new market->store cascade +
// checkbox picker, per the dispatch).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function StoreCashOnHandPage() {
  const [date, setDate] = useState(() => localToday())
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
    const qs = [`date=${date}`, resolvedStores.length && `stores=${encodeURIComponent(resolvedStores.join(','))}`].filter(Boolean).join('&')
    api(`/api/v1/closing/store-cash-on-hand?${qs}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [date, resolvedStores])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const t = data?.totals || {}

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Store', field: 'store_name', role: 'store', get: (r: any) => r.store_name },
    { header: 'Market', field: 'market', get: (r: any) => r.market || '' },
    { header: "Today's declared cash", field: 'today_declared', money: true, get: (r: any) => r.today_declared },
    { header: "Today's picked up / paid out", field: 'today_taken', money: true, get: (r: any) => r.today_taken },
    { header: 'Carryover from prior days', field: 'carryover_from_prior_days', money: true, get: (r: any) => r.carryover_from_prior_days },
    { header: 'Total cash on hand', field: 'total_cash_on_hand', money: true, get: (r: any) => r.total_cash_on_hand },
    { header: 'Last pickup at', field: 'last_pickup_at', get: (r: any) => r.last_pickup_at ? new Date(r.last_pickup_at).toLocaleString() : '' },
    { header: 'Last deposited at', field: 'last_deposited_at', get: (r: any) => r.last_deposited_at ? new Date(r.last_deposited_at).toLocaleString() : '' },
  ], [])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏦 Store Cash on Hand</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            How much cash is physically sitting in each store at the end of the day — today&apos;s declared
            cash, plus whatever&apos;s carried over from earlier days that was never picked up or paid out.
            Same math as <Link href="/closing/cash-position" style={{ color: 'var(--accent)' }}>Cash Position</Link>,
            just split into today vs. carryover.
          </p>
        </div>
        <Link href="/closing/cash-position" className="btn btn-secondary" style={{ fontSize: 13 }}>💰 Cash Position</Link>
      </div>

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <input type="date" style={sel} value={date} onChange={e => setDate(e.target.value)} />
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
            {t.stores || 0} store(s) · total cash on hand {fmt(t.total_cash_on_hand || 0)}
          </span>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : (
        <ReportShell title="Store Cash on Hand" subtitle={date}
          filename={`store-cash-on-hand_${date}`}
          columns={columns} rows={rows} stickyHeader totals />
      )}
    </div>
  )
}
