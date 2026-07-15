'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { api, localToday } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { EntityOption } from '@/components/EntityPicker'
import { EntityPickerChips } from '../_lib/EntityPickerChips'

// Cash Position report (retail-ops-7 item 5): per-store cash on hand — declared cash accumulated
// MINUS cash actually picked up, as a running ledger (a store not swept in a few days shows its TRUE
// uncollected balance, not just today's own figure). Single day -> the balance as of that day. Date
// range -> one row per (store, day-in-range) with a CUMULATIVE running column carried from an opening
// balance computed before the range start. RULE FOUR: rendered through ReportShell (Excel/PDF/Print +
// universal Send, filters/group-by for free).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function CashPositionPage() {
  const [rangeMode, setRangeMode] = useState(false)
  const [date, setDate] = useState(() => localToday())
  const [rangeStart, setRangeStart] = useState(() => localToday())
  const [rangeEnd, setRangeEnd] = useState(() => localToday())
  const [fStores, setFStores] = useState<string[]>([])
  const [fEmps, setFEmps] = useState<string[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])

  useEffect(() => {
    api('/api/v1/closing/stores').then((s: any) => setPStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {})
    api('/api/v1/storeops/employees?all_company=true').then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])
  const storeOptions: EntityOption[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, sublabel: s.market || undefined })),
    [pStores])
  const empOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name, sublabel: e.email || undefined })),
    [pEmps])

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const qs = [
      rangeMode ? `start=${rangeStart}&end=${rangeEnd}` : `date=${date}`,
      fStores.length && `stores=${encodeURIComponent(fStores.join(','))}`,
      fEmps.length && `employees=${encodeURIComponent(fEmps.join(','))}`,
    ].filter(Boolean).join('&')
    api(`/api/v1/closing/cash-position?${qs}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [rangeMode, date, rangeStart, rangeEnd, fStores, fEmps])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const mode = data?.mode || (rangeMode ? 'range' : 'single_day')

  const columns: ExportColumn[] = useMemo(() => {
    const base: ExportColumn[] = [
      { header: 'Store', field: 'store_name', get: (r: any) => r.store_name, role: 'store' },
      { header: 'Market', field: 'market', get: (r: any) => r.market || '' },
    ]
    if (mode === 'range') {
      return [
        ...base,
        { header: 'Date', field: 'close_date', type: 'date', get: (r: any) => r.close_date },
        { header: 'Declared (day)', field: 'day_declared', money: true, get: (r: any) => r.day_declared },
        { header: 'Picked up (day)', field: 'day_picked_up', money: true, get: (r: any) => r.day_picked_up },
        { header: 'Net (day)', field: 'day_net', money: true, get: (r: any) => r.day_net },
        { header: 'Cumulative cash on hand', field: 'cumulative_cash_on_hand', money: true, get: (r: any) => r.cumulative_cash_on_hand },
        { header: 'Last pickup at', field: 'last_pickup_at', get: (r: any) => r.last_pickup_at ? new Date(r.last_pickup_at).toLocaleString() : '' },
        { header: 'Last deposited at', field: 'last_deposited_at', get: (r: any) => r.last_deposited_at ? new Date(r.last_deposited_at).toLocaleString() : '' },
      ]
    }
    return [
      ...base,
      { header: 'Declared (cumulative)', field: 'declared_cumulative', money: true, get: (r: any) => r.declared_cumulative },
      { header: 'Picked up (cumulative)', field: 'picked_up_cumulative', money: true, get: (r: any) => r.picked_up_cumulative },
      { header: 'Cash on hand', field: 'cash_on_hand', money: true, get: (r: any) => r.cash_on_hand },
      { header: 'Cumulative cash on hand', field: 'cumulative_cash_on_hand', money: true, get: (r: any) => r.cumulative_cash_on_hand },
      { header: 'Last pickup at', field: 'last_pickup_at', get: (r: any) => r.last_pickup_at ? new Date(r.last_pickup_at).toLocaleString() : '' },
      { header: 'Last deposited at', field: 'last_deposited_at', get: (r: any) => r.last_deposited_at ? new Date(r.last_deposited_at).toLocaleString() : '' },
    ]
  }, [mode])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💰 Cash Position</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Per store: cash on hand as of a chosen day — declared cash accumulated minus what&apos;s
            actually been picked up — plus the last pickup / deposit times. A date range shows a running
            <strong> cumulative</strong> ledger, carried from an opening balance before the range start.
          </p>
        </div>
        <Link href="/closing/pickup" className="btn btn-secondary" style={{ fontSize: 13 }}>💵 Cash Pickup</Link>
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
        <EntityPickerChips options={storeOptions} value={fStores} onChange={setFStores} placeholder="Add a store…" width={170} />
        <EntityPickerChips options={empOptions} value={fEmps} onChange={setFEmps} placeholder="Add a rep…" width={180} />
        {(fStores.length > 0 || fEmps.length > 0) && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 9px' }} onClick={() => { setFStores([]); setFEmps([]) }}>Clear</button>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : (
        <ReportShell
          title="Cash Position" subtitle={rangeMode ? `${rangeStart} → ${rangeEnd}` : date}
          filename={`cash-position_${rangeMode ? `${rangeStart}_${rangeEnd}` : date}`}
          columns={columns} rows={rows}
        />
      )}
      {data?.opening_note && <p style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>ℹ️ {data.opening_note}</p>}
    </div>
  )
}
