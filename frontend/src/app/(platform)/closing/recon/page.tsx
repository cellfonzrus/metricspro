'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'

// Reconciliation sheet — every day's closing-vs-B2B discrepancies for the month.
// BLOCK = cash short or credit over (these stop a rep from closing). FLAG = cash over / credit
// under / count mismatch (allowed but flagged). PENDING = B2B not loaded / rep unmatched yet.
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }

const thisMonth = () => localToday().slice(0, 7)
const SEV: Record<string, { bg: string; fg: string; label: string }> = {
  block: { bg: '#fde8e8', fg: '#b42318', label: '⛔ Block' },
  flag: { bg: '#fef3e2', fg: '#b45309', label: '⚠️ Flag' },
  pending: { bg: 'var(--surface2)', fg: 'var(--text3)', label: '⏳ Pending' },
}

export default function ClosingReconPage() {
  const { user, permissions } = useAuth()
  const [period, setPeriod] = useState(thisMonth())
  const [market, setMarket] = useState('')
  const [tol, setTol] = useState('1')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'block' | 'flag' | 'pending'>('all')
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): replaces the old single-
  // select native `<select>` store filter. `market` above stays the server-side scope AUTO-default
  // (unchanged); `fMarkets`/`fStores` are the new manual, editable, MULTI-select client-side filters —
  // this endpoint returns the whole period's rows once, so narrowing by market/store is client-side
  // the same way the direction checkboxes below already work.
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])
  const [dateFilter, setDateFilter] = useState('')
  const [dirs, setDirs] = useState({ cash_over: true, cash_under: true, credit_over: true, credit_under: true })
  const [pStores, setPStores] = useState<any[]>([])

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])
  useEffect(() => { apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])

  const load = useCallback(() => {
    if (!period) return
    setLoading(true)
    api(`/api/v1/closing/recon?period=${period}&tolerance=${tol || 1}${market ? `&market=${encodeURIComponent(market)}` : ''}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, market, tol])
  useEffect(() => { load() }, [load])

  const [charged, setCharged] = useState<Record<string, boolean>>({})
  async function chargeRow(e: any) {
    const k = `${e.date}|${e.rep}|${e.metric}`
    try {
      await api('/api/v1/commcalc/chargeback-review', { method: 'POST', body: JSON.stringify({
        source: 'closing_recon', severity: e.severity === 'block' ? 'critical' : 'warning', needs_review: true,
        store: e.store_address, store_code: e.store_code, period, occurred_date: e.date,
        suggested_rep: e.rep && e.rep !== '—' ? e.rep : '', amount: Math.abs(e.variance || 0),
        detail: `Closing ${e.metric}: ${e.reason || ''}`.trim(),
        dedupe_key: `closing:${e.date}:${e.store_code}:${e.rep}:${e.metric}`,
      }) })
      setCharged(c => ({ ...c, [k]: true }))
    } catch (err: any) { alert('Charge failed: ' + (err?.message || err)) }
  }

  const s = data?.summary || {}
  const errors: any[] = data?.errors || []
  // Market joined onto each error row from the org-scoped store roster (by code, else address) — the
  // same join shape the sibling recon pages already use. Feeds both the cascade widget's options AND
  // the actual store/market narrowing below.
  const marketByKey = useMemo(() => {
    const m: Record<string, string> = {}
    for (const st of pStores) {
      if (!st.market) continue
      for (const k of [st.store_code, st.store_address, st.address]) {
        const key = (k || '').trim().toLowerCase()
        if (key && !m[key]) m[key] = st.market
      }
    }
    return m
  }, [pStores])
  const marketOf = (e: any) => marketByKey[(e.store_code || '').trim().toLowerCase()] || marketByKey[(e.store_address || '').trim().toLowerCase()] || ''
  const storesForCascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    for (const e of errors) {
      const id = (e.store_address || e.store_code || '').trim()
      if (id && !seen.has(id)) seen.set(id, { id, label: id, market: marketOf(e) || null })
    }
    return [...seen.values()]
  }, [errors, marketByKey])
  const dateOpts = Array.from(new Set(errors.map(e => e.date).filter(Boolean))).sort()
  // A money row's direction: cash short=under / cash over; credit over / credit under. Non-money rows null.
  const dirOf = (e: any): keyof typeof dirs | null => {
    if (e.variance == null) return null
    if (e.metric === 'cash') return e.variance < 0 ? 'cash_under' : 'cash_over'
    if (e.metric === 'credit') return e.variance > 0 ? 'credit_over' : 'credit_under'
    return null
  }
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])
  const fStoresFold = useMemo(() => new Set(fStores.map(s => s.trim().toLowerCase())), [fStores])
  const shown = errors.filter(e => {
    if (filter !== 'all' && e.severity !== filter) return false
    const storeKey = (e.store_address || e.store_code || '').trim().toLowerCase()
    if (fStoresFold.size && !fStoresFold.has(storeKey)) return false
    if (fMarketsFold.size && !fMarketsFold.has((marketOf(e) || '').trim().toLowerCase())) return false
    if (dateFilter && e.date !== dateFilter) return false
    const d = dirOf(e)                       // direction checkboxes only hide the money rows they name
    if (d && !dirs[d]) return false
    return true
  })
  const money = (v: any) => v == null ? '—' : fmt(v)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔎 Closing Reconciliation</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Declared closing vs B2B daily sales — every day's errors. <b>Block</b> = cash short / credit over (stops the rep closing); <b>Flag</b> = cash over / credit under / count mismatch.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <input type="month" style={sel} value={period} onChange={e => setPeriod(e.target.value)} />
        <MarketStorePicker
          stores={storesForCascade}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        <select style={sel} value={dateFilter} onChange={e => setDateFilter(e.target.value)}>
          <option value="">All dates</option>
          {dateOpts.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        {market && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Market: {market}</span>}
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>Tolerance $<input style={{ ...sel, width: 60, marginLeft: 4 }} value={tol} onChange={e => setTol(e.target.value)} /></label>
      </div>
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', padding: '8px 12px', background: 'var(--surface2)', borderRadius: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)' }}>Discrepancy:</span>
        <DirCheck label="Cash over" on={dirs.cash_over} set={v => setDirs(d => ({ ...d, cash_over: v }))} />
        <DirCheck label="Cash under" on={dirs.cash_under} set={v => setDirs(d => ({ ...d, cash_under: v }))} />
        <DirCheck label="Credit over" on={dirs.credit_over} set={v => setDirs(d => ({ ...d, credit_over: v }))} />
        <DirCheck label="Credit under" on={dirs.credit_under} set={v => setDirs(d => ({ ...d, credit_under: v }))} />
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 9px' }} onClick={() => setDirs({ cash_over: true, cash_under: true, credit_over: true, credit_under: true })}>All</button>
          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 9px' }} onClick={() => setDirs({ cash_over: false, cash_under: false, credit_over: false, credit_under: false })}>None</button>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12, marginBottom: 16 }}>
            <Tile label="Blocks" value={s.blocks || 0} tone="#b42318" onClick={() => setFilter('block')} active={filter === 'block'} />
            <Tile label="Flags" value={s.flags || 0} tone="#b45309" onClick={() => setFilter('flag')} active={filter === 'flag'} />
            <Tile label="Recon pending" value={s.pending || 0} tone="var(--text3)" onClick={() => setFilter('pending')} active={filter === 'pending'} />
            <Tile label="All rows" value={s.total || 0} tone="var(--text2)" onClick={() => setFilter('all')} active={filter === 'all'} />
          </div>

          {shown.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
              {errors.length === 0 ? 'No closing rows to reconcile for this month yet.' : 'No rows match this filter. 🎉'}
            </div>
          ) : (
            <div className="card table-wrapper" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Date', 'Store', 'Rep', 'Issue', 'Metric', 'Declared', 'B2B', 'Variance', ''].map((h, i) =>
                    <th key={i} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {shown.map((e, i) => {
                    const sv = SEV[e.severity] || SEV.flag
                    return (
                      <tr key={i}>
                        <td style={td}>{e.date}</td>
                        <td style={td}>{e.store_address || e.store_code || '—'}</td>
                        <td style={td}>{e.rep || <span style={{ color: 'var(--text3)' }}>store</span>}</td>
                        <td style={td}><span style={{ background: sv.bg, color: sv.fg, padding: '2px 8px', borderRadius: 99, fontSize: 11, fontWeight: 600 }}>{sv.label}</span></td>
                        <td style={td}>{e.metric}</td>
                        <td style={td}>{e.metric === 'activations' || e.metric === 'upgrades' ? (e.declared ?? '—') : money(e.declared)}</td>
                        <td style={td}>{e.metric === 'activations' || e.metric === 'upgrades' ? (e.b2b ?? '—') : money(e.b2b)}</td>
                        <td style={{ ...td, fontWeight: 600, color: sv.fg }}>{e.variance == null ? '—' : (e.metric === 'activations' || e.metric === 'upgrades' ? `${e.variance > 0 ? '+' : ''}${e.variance}` : `${e.variance > 0 ? '+' : ''}${fmt(e.variance)}`)}</td>
                        <td style={{ ...td, color: 'var(--text3)', whiteSpace: 'normal', maxWidth: 280 }}>
                          {e.reason}
                          {e.rep && e.rep !== '—' && (e.metric === 'cash' || e.metric === 'credit') && e.severity !== 'pending' && (
                            charged[`${e.date}|${e.rep}|${e.metric}`]
                              ? <span style={{ color: '#16794a', fontSize: 11, marginLeft: 6 }}>✓ charged</span>
                              : <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', marginLeft: 6 }} onClick={() => chargeRow(e)} title="Send to the chargeback bucket for this rep">🔻 Charge</button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

const Tile = ({ label, value, tone, onClick, active }: { label: string; value: number; tone: string; onClick: () => void; active: boolean }) => (
  <div className="card" style={{ padding: 14, cursor: 'pointer', borderColor: active ? tone : undefined, borderWidth: active ? 2 : 1, borderStyle: 'solid' }} onClick={onClick}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2, color: tone }}>{value}</div>
  </div>
)

const DirCheck = ({ label, on, set }: { label: string; on: boolean; set: (v: boolean) => void }) => (
  <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}>
    <input type="checkbox" checked={on} onChange={e => set(e.target.checked)} /> {label}
  </label>
)
